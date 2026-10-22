"""Versioned portable PostgreSQL backups. No secrets or active sessions."""
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import time
import zipfile
from urllib.parse import urlsplit
import httpx
from psycopg import sql
from infrastructure import database,REFERENCE_LOCK
from media_store import referenced_urls

TABLES={
 'mezcales':['id','nombre','precio_centavos','descripcion','link_venta','imagen_url','creado_en','actualizado_en','extra_json'],
 'site_content':['key','value'],
 'studio':['id','draft','published','version','published_at'],
 'studio_history':['id','document','published_at'],
 'metadata':['key','value'],
 'media_assets':['id','sha256','public_id','asset_id','url','width','height','bytes','created_at','deleted_at'],
 'audit_log':['id','action','entity','actor','created_at'],
}

def create_backup(app,path,include_images=False):
    with database(app) as (_,cursor):
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        tables={}
        for table,columns in TABLES.items():
            cursor.execute(sql.SQL('SELECT {} FROM {} ORDER BY {}').format(sql.SQL(',').join(map(sql.Identifier,columns)),sql.Identifier(table),sql.Identifier(columns[0])))
            tables[table]=cursor.fetchall()
        refs=sorted(referenced_urls(cursor) | {r['url'] for r in tables['media_assets'] if r['deleted_at'] is None})
    content=json.dumps(tables,ensure_ascii=False,separators=(',',':')).encode()
    if len(content)>50*1024*1024:raise ValueError('El respaldo supera 50 MB de datos; usa pg_dump para esta base.')
    manifest={'format':'valerion-web','version':3,'created_at':int(time.time()),'schema_version':'3',
              'data_sha256':hashlib.sha256(content).hexdigest(),'counts':{k:len(v) for k,v in tables.items()},
              'include_images':include_images,'images':[],'external_references':[]}
    total=0
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('data.json',content)
        with (httpx.Client(timeout=httpx.Timeout(20,connect=8),follow_redirects=False) if include_images else nullcontext()) as client:
            for url in refs:
                parsed=urlsplit(url)
                owned=parsed.scheme=='https' and parsed.hostname=='res.cloudinary.com' and parsed.path.startswith('/'+app.state.cloud['cloud_name']+'/image/upload/') and not parsed.username
                if not owned:
                    if not url.startswith('/static/'):manifest['external_references'].append(url)
                    continue
                entry={'url':url,'path':None,'sha256':None}
                if include_images:
                    name='images/'+hashlib.sha256(url.encode()).hexdigest()+'.bin'
                    digest=hashlib.sha256();size=0
                    with client.stream('GET',url) as response:
                        response.raise_for_status()
                        if not response.headers.get('content-type','').startswith('image/'):
                            raise ValueError('Cloudinary devolvió un archivo no reconocido como imagen.')
                        with archive.open(name,'w') as out:
                            for chunk in response.iter_bytes(65536):
                                size+=len(chunk);total+=len(chunk)
                                if size>30*1024*1024 or total>200*1024*1024:
                                    raise ValueError('Las imágenes exceden el límite de este exportador; usa una exportación externa de Cloudinary.')
                                digest.update(chunk);out.write(chunk)
                    entry.update(path=name,sha256=digest.hexdigest())
                manifest['images'].append(entry)
        archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        archive.writestr('LEEME.txt','Respaldo Valerion WEB 3.0. Datos completos de la aplicación; sin contraseñas ni sesiones.\n'+('Incluye copias de las imágenes referenciadas de la cuenta Cloudinary configurada.\n' if include_images else 'Incluye referencias de imágenes; NO incluye archivos de Cloudinary.\n')+'Las imágenes externas y las rutas /media/ se indican en external_references. Consulta docs/RESPALDOS.md.\n')
    return manifest

def validate_backup(path):
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)) or sum(i.file_size for i in archive.infolist())>300*1024*1024:
            raise ValueError('Respaldo inválido o demasiado grande.')
        import re
        if any(n not in {'manifest.json','data.json','LEEME.txt'} and not re.fullmatch(r'images/[a-f0-9]{64}\.bin',n) for n in names):
            raise ValueError('El archivo contiene rutas no permitidas.')
        if archive.getinfo('data.json').file_size>50*1024*1024:raise ValueError('Datos demasiado grandes.')
        manifest=json.loads(archive.read('manifest.json'));raw=archive.read('data.json')
        if manifest.get('format')!='valerion-web' or manifest.get('version')!=3 or hashlib.sha256(raw).hexdigest()!=manifest.get('data_sha256'):
            raise ValueError('Formato o integridad del respaldo inválidos.')
        tables=json.loads(raw)
        if set(tables)!=set(TABLES):raise ValueError('Faltan tablas en el respaldo.')
        from main import ProductInput,ContentInput
        from cms import Studio
        for table,rows in tables.items():
            if not isinstance(rows,list) or len(rows)!=manifest['counts'][table]:raise ValueError('Conteo inválido.')
            ids=set()
            for row in rows:
                if set(row)!=set(TABLES[table]):raise ValueError('Columnas inválidas.')
                identity=row[TABLES[table][0]]
                if identity in ids:raise ValueError('Identificador duplicado.')
                ids.add(identity)
        if len(tables['studio'])!=1 or tables['studio'][0]['id']!=1:raise ValueError('Configuración del editor inválida.')
        for row in tables['mezcales']:
            if isinstance(row['precio_centavos'],bool) or not isinstance(row['precio_centavos'],int):raise ValueError('Precio inválido.')
            from decimal import Decimal
            ProductInput(nombre=row['nombre'],precio=Decimal(row['precio_centavos'])/100,descripcion=row['descripcion'],link_venta=row['link_venta'],imagen_url=row['imagen_url'],**json.loads(row['extra_json']))
        from content import FIELD_MAP
        known={r['key']:r['value'] for r in tables['site_content'] if r['key'] in FIELD_MAP}
        if known:ContentInput(valores=known)
        for row in tables['studio']:
            Studio.model_validate_json(row['draft']);Studio.model_validate_json(row['published'])
        for row in tables['studio_history']:Studio.model_validate_json(row['document'])
        for entry in manifest['images']:
            if entry['path']:
                if entry['path'] not in names or hashlib.sha256(archive.read(entry['path'])).hexdigest()!=entry['sha256']:raise ValueError('Imagen dañada o ausente.')
    return manifest,tables

def restore_tables(app,tables):
    # Offline maintenance only: app workers must be stopped before replacement.
    with database(app,write=True) as (_,cursor):
        cursor.execute('SET LOCAL statement_timeout=\'120000\'')
        cursor.execute("SELECT to_regclass('clientes') AS clients,to_regclass('ordenes') AS orders")
        legacy=cursor.fetchone()
        if legacy['orders']:
            raise ValueError('Usa una restauración completa de PostgreSQL: existen tablas de módulos históricos.')
        if legacy['clients']:
            cursor.execute('SELECT EXISTS(SELECT 1 FROM clientes) AS populated')
            if cursor.fetchone()['populated']:
                raise ValueError('Usa una restauración completa de PostgreSQL: existen cuentas de clientes.')

        for table in reversed(TABLES):cursor.execute(sql.SQL('DELETE FROM {}').format(sql.Identifier(table)))
        cursor.execute('DELETE FROM sessions');cursor.execute('DELETE FROM login_attempts')
        for table,columns in TABLES.items():
            for offset in range(0,len(tables[table]),100):
                rows=tables[table][offset:offset+100]
                group=sql.SQL('({})').format(sql.SQL(',').join(sql.Placeholder() for _ in columns))
                statement=sql.SQL('INSERT INTO {} ({}) VALUES {}').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(group for _ in rows))
                cursor.execute(statement,[row[c] for row in rows for c in columns])
        for table in ['mezcales','studio_history','media_assets','audit_log']:
            cursor.execute(sql.SQL("SELECT setval(pg_get_serial_sequence(%s,'id'),COALESCE(MAX(id),1),COUNT(*)>0) FROM {}").format(sql.Identifier(table)),(table,))
    app.state.content_cache.clear();app.state.catalog_cache.clear()
