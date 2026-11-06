from infrastructure import database
from accounts import migrate

def register(client,email='persona@example.com',name='Persona de prueba'):
    r=client.post('/api/clientes/registro',json={'nombre':name,'email':email,'password':'UnaClave-Larga-123'})
    assert r.status_code==201,r.text
    return {'X-CSRF-Token':r.json()['csrf_token']}

def test_profile_and_password_revocation(client):
    h=register(client)
    me=client.get('/api/clientes/me').json();assert me['nombre']=='Persona de prueba'
    assert not {'password_hash','password_salt','token_hash'}&set(me)
    assert client.put('/api/clientes/perfil',json={'nombre':'Nuevo nombre'}).status_code==403
    assert client.put('/api/clientes/perfil',headers=h,json={'nombre':'Nuevo nombre','preferencia_contacto':True}).status_code==200
    assert client.get('/api/clientes/me').json()['preferencia_contacto']
    assert client.post('/api/clientes/password',headers=h,json={'actual':'wrong','nueva':'NuevaClaveLarga123'}).status_code==400
    assert client.post('/api/clientes/password',headers=h,json={'actual':'UnaClave-Larga-123','nueva':'NuevaClaveLarga123'}).status_code==200
    assert client.get('/api/clientes/me').status_code==401
    assert client.post('/api/clientes/login',json={'email':'persona@example.com','password':'UnaClave-Larga-123'}).status_code==401
    assert client.post('/api/clientes/login',json={'email':'persona@example.com','password':'NuevaClaveLarga123'}).status_code==200

def test_roles_and_admin_suspend(client,headers):
    h=register(client)
    me=client.get('/api/clientes/me').json()
    r=client.get('/api/admin/clientes');assert r.status_code==200
    assert 'password_hash' not in r.text
    assert client.patch('/api/admin/clientes/'+str(me['id']),headers=h,json={'activo':False}).status_code==403
    assert client.patch('/api/admin/clientes/'+str(me['id']),headers=headers,json={'activo':False}).status_code==200
    assert client.get('/api/clientes/me').status_code==401
    assert client.post('/api/clientes/login',json={'email':'persona@example.com','password':'UnaClave-Larga-123'}).status_code==401
    client.cookies.clear();h=register(client,'otra@example.com')
    assert client.get('/api/admin/clientes').status_code==401
    assert client.get('/api/admin/mezcales').status_code==401

def test_login_throttle_and_origin(client):
    for _ in range(10):assert client.post('/api/clientes/login',json={'email':'a@example.com','password':'bad'}).status_code==401
    assert client.post('/api/clientes/login',json={'email':'a@example.com','password':'bad'}).status_code==429
    assert client.post('/api/clientes/registro',headers={'Origin':'https://otro.example'},json={'nombre':'Nombre','email':'b@example.com','password':'UnaClave-Larga-123'}).status_code==403

def test_account_isolation_and_sessions(client):
    h1=register(client);first=client.get('/api/clientes/me').json()['id']
    h2=register(client,'second@example.com','Segunda persona')
    assert client.get('/api/clientes/me').json()['id']!=first
    assert client.put('/api/clientes/perfil',headers=h2,json={'nombre':'Inválido','id':first}).status_code==422
    assert client.post('/api/clientes/logout',json={}).status_code==403
    assert client.post('/api/clientes/logout',json={},headers=h2).status_code==204
    assert client.get('/api/clientes/me').status_code==401
    with database(client.app) as (_,cur):
        cur.execute('SELECT nombre FROM clientes WHERE id=%s',(first,));assert cur.fetchone()['nombre']=='Persona de prueba'

def test_migration_preserves_legacy_accounts_revokes_old_sessions(client):
    h=register(client)
    me=client.get('/api/clientes/me').json()
    with database(client.app) as (_,cur):
        cur.execute('UPDATE customer_sessions SET csrf=NULL');migrate(cur)
        cur.execute('SELECT count(*) AS n FROM clientes');assert cur.fetchone()['n']==1
    assert client.get('/api/clientes/me').status_code==401
    assert client.post('/api/clientes/login',json={'email':me['email'],'password':'UnaClave-Larga-123'}).status_code==200

def test_admin_revoke_and_no_checkout(client,headers):
    assert client.post('/api/admin/sesiones/revocar',json={}).status_code==403
    assert client.post('/api/admin/sesiones/revocar',json={},headers=headers).status_code==204
    assert client.get('/api/auth/me').status_code==401
    assert client.post('/api/checkout',json={}).status_code==404
    assert client.post('/api/pagos',json={}).status_code==404

def test_email_and_profile_validation(client):
    assert client.post('/api/clientes/registro',json={'nombre':'Persona','email':'no-email','password':'UnaClave-Larga-123'}).status_code==422
    h=register(client,' PERSONA@EXAMPLE.COM ')
    assert client.get('/api/clientes/me').json()['email']=='persona@example.com'
    assert client.put('/api/clientes/perfil',headers=h,json={'nombre':'  '}).status_code==422
    assert client.post('/api/clientes/registro',json={'nombre':'Otra persona','email':'persona@example.com','password':'UnaClave-Larga-123'}).status_code==409
    assert client.get('/cuenta').status_code==200
    assert client.get('/cuenta/registro').status_code==200

def test_site_restore_refuses_customer_data(client,tmp_path):
    from backups import create_backup,validate_backup,restore_tables
    import pytest
    register(client)
    path=tmp_path/'site.zip';create_backup(client.app,path);_,tables=validate_backup(path)
    assert 'clientes' not in tables
    with pytest.raises(ValueError,match='cuentas de clientes'):restore_tables(client.app,tables)
    assert client.get('/api/clientes/me').status_code==200
