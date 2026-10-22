"""Validated Cloudinary assets, deterministic uploads and explicit safe cleanup."""
import hashlib
from io import BytesIO
import re
import time
import warnings
from urllib.parse import urlsplit,unquote
import cloudinary.uploader
from fastapi import HTTPException
from PIL import Image,ImageOps,UnidentifiedImageError
from infrastructure import database,audit

MAX_UPLOAD=8*1024*1024

def cloud_settings(url):
    parsed=urlsplit(url or '')
    if parsed.scheme!='cloudinary' or not parsed.username or not parsed.password or not parsed.hostname:
        raise ValueError('Configura CLOUDINARY_URL desde las variables de tu cuenta.')
    return {'cloud_name':parsed.hostname,'api_key':unquote(parsed.username),'api_secret':unquote(parsed.password),'secure':True}

def normalize(raw):
    if len(raw)>MAX_UPLOAD:raise HTTPException(413,'La imagen supera el límite de 8 MB.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as img:
                if img.format not in {'JPEG','PNG','WEBP'} or img.width*img.height>20_000_000:
                    raise HTTPException(422,'Usa JPG, PNG o WebP de hasta 20 megapíxeles.')
                img.verify()
            with Image.open(BytesIO(raw)) as img:
                oriented=ImageOps.exif_transpose(img)
                image=oriented.convert('RGBA' if 'A' in oriented.getbands() or 'transparency' in oriented.info else 'RGB')
                image.thumbnail((2400,2400));image.info.clear()
                out=BytesIO();image.save(out,'WEBP',quality=85,method=4,exif=b'')
                return out.getvalue(),image.width,image.height
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombWarning,Image.DecompressionBombError):
        raise HTTPException(422,'El archivo no es una imagen válida.')

def store_upload(app,raw):
    data,width,height=normalize(raw);digest=hashlib.sha256(data).hexdigest()
    # Serialize upload/registration with cleanup and reference mutations.
    with database(app,write=True) as (_,cursor):
        cursor.execute('SELECT * FROM media_assets WHERE sha256=%s AND deleted_at IS NULL',(digest,));old=cursor.fetchone()
        if old:return {'url':old['url'],'ancho':old['width'],'alto':old['height'],'reutilizada':True,'id':old['id']}
        public_id=app.state.media_folder+'/'+digest
        try:
            response=app.state.cloud_uploader(BytesIO(data),public_id=public_id,overwrite=False,
                resource_type='image',type='upload',format='webp',timeout=30,**app.state.cloud)
            url=response['secure_url'];parsed=urlsplit(url)
            if parsed.scheme!='https' or parsed.hostname!='res.cloudinary.com' or not parsed.path.startswith('/'+app.state.cloud['cloud_name']+'/image/upload/'):
                raise ValueError('Unexpected upload destination')
            if response['public_id']!=public_id:raise ValueError('Unexpected asset identifier')
        except Exception:
            raise HTTPException(502,'Cloudinary no pudo completar la carga. Intenta nuevamente; tu producto no se modificó.')
        cursor.execute('''INSERT INTO media_assets(sha256,public_id,asset_id,url,width,height,bytes,created_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(sha256) DO UPDATE SET
            url=EXCLUDED.url,asset_id=EXCLUDED.asset_id,public_id=EXCLUDED.public_id,created_at=EXCLUDED.created_at,deleted_at=NULL RETURNING *''',
            (digest,public_id,response.get('asset_id'),url,width,height,len(data),int(time.time())))
        row=cursor.fetchone();audit(cursor,'media.upload',str(row['id']),app.state.username)
    return {'url':row['url'],'ancho':row['width'],'alto':row['height'],'reutilizada':False,'id':row['id']}

def optimized_url(url,width=640):
    if width not in (160,640,1280):width=640
    parsed=urlsplit(str(url))
    if parsed.hostname!='res.cloudinary.com' or '/image/upload/' not in parsed.path:return str(url)
    # Transform only standard versioned upload URLs, never signed or already-transformed URLs.
    prefix,suffix=str(url).split('/image/upload/',1)
    if not re.match(r'v\d+/',suffix):return str(url)
    return prefix+'/image/upload/f_auto,q_auto,c_limit,w_'+str(width)+'/'+suffix

def walk_strings(value):
    if isinstance(value,str):yield value
    elif isinstance(value,dict):
        for v in value.values():yield from walk_strings(v)
    elif isinstance(value,list):
        for v in value:yield from walk_strings(v)

def referenced_urls(cursor):
    import json
    refs=set()
    cursor.execute('SELECT imagen_url,extra_json FROM mezcales')
    for row in cursor.fetchall():
        refs.add(row['imagen_url']);refs.update(walk_strings(json.loads(row['extra_json'])))
    cursor.execute('SELECT value FROM site_content')
    refs.update(r['value'] for r in cursor.fetchall())
    cursor.execute('SELECT draft,published FROM studio')
    for r in cursor.fetchall():
        for k in ['draft','published']:refs.update(walk_strings(json.loads(r[k])))
    cursor.execute('SELECT document FROM studio_history')
    for r in cursor.fetchall():refs.update(walk_strings(json.loads(r['document'])))
    return {s for s in refs if s.startswith(('https://','/media/','/static/'))}

def asset_key(url):
    parsed=urlsplit(url)
    if parsed.hostname!='res.cloudinary.com' or '/image/upload/' not in parsed.path:return url
    prefix,suffix=parsed.path.split('/image/upload/',1)
    match=re.search(r'(?:^|/)v[0-9]+/(.+)$',suffix)
    if not match:return url
    return prefix+'/'+re.sub(r'\.(?:jpg|jpeg|png|webp|avif|gif)$','',unquote(match.group(1)),flags=re.I)

def is_referenced(url,refs):
    key=asset_key(url)
    if any(asset_key(ref)==key for ref in refs):return True
    parsed=urlsplit(url)
    if parsed.hostname=='res.cloudinary.com' and key!=url:
        cloud,public_id=key.lstrip('/').split('/',1)
        prefix='/'+cloud+'/image/upload/'
        for ref in refs:
            candidate=urlsplit(ref)
            path=re.sub(r'\.(?:jpg|jpeg|png|webp|avif|gif)$','',unquote(candidate.path),flags=re.I)
            if candidate.hostname==parsed.hostname and path.startswith(prefix) and (path[len(prefix):]==public_id or path.endswith('/'+public_id)):return True
    return False

def usage(cursor):
    refs=referenced_urls(cursor)
    cursor.execute('SELECT id,url,public_id,width,height,bytes,created_at FROM media_assets WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 500')
    return [{**r,'referenced':is_referenced(r['url'],refs),'can_delete':not is_referenced(r['url'],refs) and r['created_at']<int(time.time())-86400} for r in cursor.fetchall()]

def delete_unused(app,asset_id):
    with database(app,write=True) as (_,cursor):
        cursor.execute('SELECT * FROM media_assets WHERE id=%s AND deleted_at IS NULL FOR UPDATE',(asset_id,));row=cursor.fetchone()
        if not row:raise HTTPException(404,'Imagen no encontrada.')
        if is_referenced(row['url'],referenced_urls(cursor)):raise HTTPException(409,'La imagen está en uso en un producto, contenido, borrador o historial.')
        if row['created_at']>int(time.time())-86400:raise HTTPException(409,'Espera 24 horas después de la carga antes de eliminarla.')
        if not row['public_id'].startswith(app.state.media_folder+'/'):raise HTTPException(409,'La imagen pertenece a otra carpeta.')
        try:
            result=app.state.cloud_destroy(row['public_id'],resource_type='image',type='upload',invalidate=True,timeout=20,**app.state.cloud)
            if result.get('result') not in ['ok','not found']:raise ValueError('delete failed')
        except Exception:raise HTTPException(502,'No se pudo eliminar en Cloudinary; vuelve a intentarlo.')
        cursor.execute('UPDATE media_assets SET deleted_at=%s WHERE id=%s',(int(time.time()),asset_id))
        audit(cursor,'media.delete',str(asset_id),app.state.username)
