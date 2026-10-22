"""Validate first; offline transactional restore with an automatic pre-restore copy."""
import argparse
from datetime import datetime,timezone
from pathlib import Path
import json
import hashlib
import time
import uuid
import zipfile
from io import BytesIO
import cloudinary.uploader
from backups import create_backup,validate_backup,restore_tables
from maintenance import maintenance

def reupload(app,manifest,tables,path):
    mapping={};recovered=[]
    with zipfile.ZipFile(path) as archive:
        for entry in manifest['images']:
            if not entry['path']:raise ValueError('Se requiere un respaldo con imágenes para recargarlas.')
            raw=archive.read(entry['path'])
            result=cloudinary.uploader.upload(BytesIO(raw),resource_type='image',type='upload',public_id=app.state.media_folder+'/recuperado_'+uuid.uuid4().hex,overwrite=False,timeout=30,**app.state.cloud)
            from urllib.parse import urlsplit
            dest=urlsplit(result['secure_url'])
            if dest.scheme!='https' or dest.hostname!='res.cloudinary.com' or not dest.path.startswith('/'+app.state.cloud['cloud_name']+'/image/upload/'):raise ValueError('Respuesta de Cloudinary inválida.')
            mapping[entry['url']]=result['secure_url']
            old=next((r for r in tables['media_assets'] if r['url']==entry['url']),None)
            recovered.append({'id':len(recovered)+1,'sha256':old['sha256'] if old else hashlib.sha256(raw).hexdigest(),'public_id':result['public_id'],'asset_id':result.get('asset_id'),'url':result['secure_url'],'width':result['width'],'height':result['height'],'bytes':result.get('bytes',len(raw)),'created_at':int(time.time()),'deleted_at':None})
    def replace(value):
        if isinstance(value,str):return mapping.get(value,value)
        if isinstance(value,list):return [replace(v) for v in value]
        if isinstance(value,dict):return {k:replace(v) for k,v in value.items()}
        return value
    for row in tables['mezcales']:
        row['imagen_url']=replace(row['imagen_url']);row['extra_json']=json.dumps(replace(json.loads(row['extra_json'])),ensure_ascii=False)
    for row in tables['site_content']:row['value']=replace(row['value'])
    for row in tables['studio']:
        for key in ('draft','published'):row[key]=json.dumps(replace(json.loads(row[key])),ensure_ascii=False)
    for row in tables['studio_history']:row['document']=json.dumps(replace(json.loads(row['document'])),ensure_ascii=False)
    # Recovered media use new identifiers: don't retain obsolete deletion metadata.
    tables['media_assets']=list({r['sha256']:r for r in recovered}.values())
    return tables

def main():
    parser=argparse.ArgumentParser(description='Valida o restaura un respaldo WEB 3.0. Detén todos los servidores antes de aplicar.')
    parser.add_argument('respaldo',type=Path);parser.add_argument('--aplicar',action='store_true');parser.add_argument('--servidor-detenido',action='store_true');parser.add_argument('--recargar-imagenes',action='store_true')
    args=parser.parse_args()
    try:
        manifest,tables=validate_backup(args.respaldo)
        print('Integridad y estructura verificadas:',manifest['counts'])
        if not args.aplicar:
            print('Solo validación. No se modificó PostgreSQL ni Cloudinary.');return
        if not args.servidor_detenido:parser.error('Detén todos los servidores y confirma con --servidor-detenido.')
        if args.recargar_imagenes and not manifest['include_images']:parser.error('El respaldo no incluye archivos de imágenes.')
        before=args.respaldo.resolve().parent/('antes_restaurar_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'.zip')
        with maintenance() as app:
            create_backup(app,before,False);validate_backup(before)
            print('Copia previa guardada:',before)
            if args.recargar_imagenes:tables=reupload(app,manifest,tables,args.respaldo)
            restore_tables(app,tables)
        print('Restauración terminada. Reinicia el servidor y vuelve a iniciar sesión.')
    except Exception as exc:
        raise SystemExit('No se completó la restauración ('+type(exc).__name__+'). Los cambios de tablas se revierten si falla su transacción. Las imágenes recargadas en Cloudinary pueden permanecer; consulta docs/RESPALDOS.md.') from None
if __name__=='__main__':main()
