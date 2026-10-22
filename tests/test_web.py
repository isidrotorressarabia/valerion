from io import BytesIO
from pathlib import Path
import hashlib
import json
import time
import zipfile
from PIL import Image
from conftest import product,PASSWORD
from infrastructure import database
from backups import create_backup,validate_backup,restore_tables
from main import initialize
import pytest

def create(client,headers,**changes):
    response=client.post('/api/mezcales',json=product(**changes),headers=headers)
    assert response.status_code==201,response.text
    return response.json()

def photo():
    out=BytesIO();Image.new('RGB',(160,120),'#135531').save(out,'PNG');return out.getvalue()

def cloud(client):
    calls=[]
    def upload(data,**kw):
        calls.append(kw);return {'public_id':kw['public_id'],'asset_id':'test-asset','secure_url':'https://res.cloudinary.com/demo/image/upload/v1/'+kw['public_id']+'.webp'}
    client.app.state.cloud_uploader=upload
    client.app.state.cloud_destroy=lambda *a,**k:{'result':'ok'}
    return calls

def upload(client,headers):
    r=client.post('/api/imagenes',headers=headers,files={'archivo':('foto.png',photo(),'image/png')})
    assert r.status_code==201,r.text
    return r.json()

def test_catalog_sql_pagination_filters_and_drafts(client,headers):
    for n in range(15):create(client,headers,nombre=f'Mezcal {n:02}',precio=str(100+n),category='Espadín',featured=n==0)
    hidden=create(client,headers,nombre='Oculto',published=False)
    data=client.get('/api/mezcales?sort=destacados&page_size=5').json()
    assert data['total']==15 and len(data['items'])==5 and data['pages']==3
    assert data['items'][0]['nombre']=='Mezcal 00'
    assert client.get('/api/mezcales?page=3&page_size=5').json()['items'][0]['nombre']=='Mezcal 05'
    assert client.get('/api/mezcales?q=Mezcal%2001&category=Espad%C3%ADn').json()['total']==1
    assert client.get('/api/mezcales?q=%25').json()['total']==0
    assert client.get('/api/mezcales/'+str(hidden['id'])).status_code==404
    assert client.get('/api/admin/mezcales').json()['total']==16
    assert client.get('/api/mezcales?page_size=500').status_code==422

def test_gallery_edit_detail_and_cache_invalidation(client,headers):
    assert client.get('/api/mezcales').json()['total']==0
    p=create(client,headers,gallery=[{'url':'/static/img/bottle.svg','alt':'Detalle'}],blocks=[{'kind':'image','image':'/static/img/bottle.svg'}])
    assert client.get('/api/mezcales').json()['total']==1
    assert client.get('/api/mezcales').json()['items'][0]['gallery']==[]
    assert len(client.get('/api/admin/mezcales/'+str(p['id'])).json()['gallery'])==1
    assert client.get('/producto/'+str(p['id'])).status_code==200
    r=client.put('/api/mezcales/'+str(p['id']),headers=headers,json=product(published=False));assert r.status_code==200
    assert client.get('/api/mezcales').json()['total']==0
    assert client.app.state.catalog_cache.stats()['hits']>0

def test_login_rate_limit_commits_and_logout(client):
    for _ in range(5):assert client.post('/api/auth/login',json={'usuario':'admin','password':'wrong'}).status_code==401
    assert client.post('/api/auth/login',json={'usuario':'admin','password':PASSWORD}).status_code==429
    with database(client.app) as (_,cur):
        cur.execute('SELECT failures FROM login_attempts');assert cur.fetchone()['failures']==5
        cur.execute('UPDATE login_attempts SET last_attempt=0')
    response=client.post('/api/auth/login',json={'usuario':'admin','password':PASSWORD});assert response.status_code==200
    assert 'HttpOnly' in response.headers['set-cookie']
    assert client.post('/api/auth/logout',json={},headers={'X-CSRF-Token':response.json()['csrf_token']}).status_code==204
    assert client.get('/api/auth/me').status_code==401

def test_csrf_origin_and_draft_privacy(client,headers):
    assert client.post('/api/mezcales',json=product()).status_code==403
    assert client.post('/api/mezcales',json=product(),headers={**headers,'Origin':'https://other.example'}).status_code==403
    assert client.post('/api/mezcales',content='{}',headers=headers).status_code==415
    client.cookies.clear()
    assert client.get('/theme.css?draft=true').status_code==401
    assert client.get('/api/admin/respaldo').status_code==401
    assert client.get('/api/admin/sistema').status_code==401

def test_studio_publish_conflict_history_and_contact(client,headers):
    body=client.get('/api/admin/estudio').json();body.pop('published_at',None)
    body['document']['sections'].append({'id':'nueva','kind':'text','title':'Nuestra nueva historia'})
    body['document']['contact'].update(whatsapp='525512345678',emails=[{'value':'contacto@example.com'}],socials=[{'platform':'Instagram','url':'https://instagram.com/example'}])
    saved=client.put('/api/admin/estudio',json=body,headers=headers);assert saved.status_code==200,saved.text
    assert 'Nuestra nueva historia' not in client.get('/').text
    assert 'Nuestra nueva historia' in client.get('/admin/preview').text
    assert client.put('/api/admin/estudio',json=body,headers=headers).status_code==409
    pub=client.post('/api/admin/estudio/publicar',headers=headers,json={'version':saved.json()['version']});assert pub.status_code==200
    html=client.get('/').text;assert 'Nuestra nueva historia' in html and 'wa.me/525512345678' in html and 'mailto:contacto@example.com' in html
    assert len(client.get('/api/admin/estudio/historial').json())==1

@pytest.mark.parametrize('field,value',[('imagen_url','javascript:alert(1)'),('link_venta','http://example.com'),('precio','0'),('precio','10.123')])
def test_invalid_products(client,headers,field,value):
    assert client.post('/api/mezcales',json=product(**{field:value}),headers=headers).status_code==422

def test_cloudinary_normalizes_deduplicates_and_distinguishes_errors(client,headers):
    calls=cloud(client);first=upload(client,headers);second=upload(client,headers)
    assert first['url']==second['url'] and second['reutilizada'] and len(calls)==1
    assert calls[0]['overwrite'] is False
    r=client.post('/api/imagenes',headers=headers,files={'archivo':('bad.png',b'not an image','image/png')});assert r.status_code==422
    with database(client.app) as (_,cur):cur.execute('DELETE FROM media_assets')
    def fail(*a,**kw):raise TimeoutError('private-secret')
    client.app.state.cloud_uploader=fail
    r=client.post('/api/imagenes',headers=headers,files={'archivo':('x.png',photo(),'image/png')})
    assert r.status_code==502 and 'private-secret' not in r.text

def test_cleanup_protects_products_and_grace_period(client,headers):
    cloud(client);asset=upload(client,headers)
    endpoint='/api/admin/imagenes/'+str(asset['id'])
    assert client.delete(endpoint,headers=headers).status_code==409
    with database(client.app) as (_,cur):cur.execute('UPDATE media_assets SET created_at=1')
    p=create(client,headers,imagen_url=asset['url'])
    assert client.delete(endpoint,headers=headers).status_code==409
    assert client.delete('/api/mezcales/'+str(p['id']),headers=headers).status_code==204
    assert client.delete(endpoint,headers=headers).status_code==204
    assert client.post('/api/mezcales',json=product(imagen_url=asset['url']),headers=headers).status_code==422

def test_backup_restore_all_tables_integrity_and_sequences(client,headers,tmp_path):
    p=create(client,headers)
    path=tmp_path/'backup.zip';create_backup(client.app,path)
    manifest,tables=validate_backup(path)
    assert set(tables)=={'mezcales','studio','studio_history','site_content','metadata','media_assets','audit_log'}
    assert 'sessions' not in tables and tables['audit_log']
    create(client,headers,nombre='Otro producto');restore_tables(client.app,tables)
    assert client.get('/api/auth/me').status_code==401
    assert client.get('/api/mezcales').json()['total']==1
    response=client.post('/api/auth/login',json={'usuario':'admin','password':PASSWORD})
    new=create(client,{'X-CSRF-Token':response.json()['csrf_token']})
    assert new['id']>p['id']
    bad=tmp_path/'bad.zip'
    with zipfile.ZipFile(path) as src,zipfile.ZipFile(bad,'w') as out:
        for name in src.namelist():out.writestr(name,b'{}' if name=='data.json' else src.read(name))
    with pytest.raises(ValueError):validate_backup(bad)

def test_restore_failure_rolls_back(client,headers,tmp_path):
    create(client,headers);path=tmp_path/'backup.zip';create_backup(client.app,path);_,tables=validate_backup(path)
    tables['mezcales'][0]['precio_centavos']=-10
    with pytest.raises(Exception):restore_tables(client.app,tables)
    assert client.get('/api/mezcales').json()['total']==1
    assert client.get('/api/auth/me').status_code==200

def test_idempotent_migration_pool_and_health(client,headers):
    create(client,headers);initialize(client.app)
    assert client.get('/api/mezcales').json()['total']==1
    for _ in range(8):assert client.get('/ready').status_code==200
    stats=client.app.state.pool.get_stats();assert stats['pool_size']<=1 and stats['connections_num']<=2
    assert client.get('/admin/sistema').status_code==200
    assert client.get('/health').json()=={'status':'ok'}
    with database(client.app) as (_,cur):
        cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname=current_schema()")
        assert 'catalog_public_price_idx' in {r['indexname'] for r in cur.fetchall()}

def test_backup_includes_bytes_and_reuploads_with_url_mapping(client,headers,tmp_path,monkeypatch):
    cloud(client);asset=upload(client,headers);create(client,headers,imagen_url=asset['url'],gallery=[{'url':asset['url']}])
    import httpx
    original=httpx.Client
    transport=httpx.MockTransport(lambda request:httpx.Response(200,headers={'content-type':'image/png'},content=photo()))
    monkeypatch.setattr('backups.httpx.Client',lambda **kw:original(transport=transport,trust_env=False,**kw))
    path=tmp_path/'images.zip';manifest=create_backup(client.app,path,True);_,tables=validate_backup(path)
    assert len(manifest['images'])==1 and manifest['images'][0]['sha256']==hashlib.sha256(photo()).hexdigest()
    def recovered(data,**kw):return {'secure_url':'https://res.cloudinary.com/demo/image/upload/v2/'+kw['public_id']+'.png','public_id':kw['public_id'],'width':160,'height':120,'bytes':100}
    monkeypatch.setattr('cloudinary.uploader.upload',recovered)
    from restaurar import reupload
    tables=reupload(client.app,manifest,tables,path)
    assert tables['mezcales'][0]['imagen_url']!=asset['url']
    assert tables['mezcales'][0]['imagen_url']==tables['media_assets'][0]['url']
    assert tables['media_assets'][0]['public_id'].startswith(client.app.state.media_folder+'/')
    restore_tables(client.app,tables)
    assert client.get('/api/mezcales').json()['items'][0]['imagen_url']==tables['media_assets'][0]['url']

def test_cleanup_protects_transformed_image_in_history(client,headers):
    cloud(client);asset=upload(client,headers)
    doc=client.get('/api/admin/estudio').json()['document']
    doc['sections'].append({'id':'foto','kind':'image_text','image':asset['url'].replace('/upload/','/upload/w_640/')})
    with database(client.app) as (_,cur):
        cur.execute('UPDATE media_assets SET created_at=1')
        cur.execute('INSERT INTO studio_history(document,published_at) VALUES(%s,%s)',(json.dumps(doc),'2026-10-21T00:00:00Z'))
    assert client.delete('/api/admin/imagenes/'+str(asset['id']),headers=headers).status_code==409
