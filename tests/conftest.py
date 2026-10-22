import os
import uuid
import pytest
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient
from main import create_app

PASSWORD='test-password-123'
@pytest.fixture
def client(monkeypatch):
    url=os.environ.get('TEST_DATABASE_URL')
    if not url:pytest.skip('Configura TEST_DATABASE_URL con PostgreSQL exclusivo para pruebas.')
    if url==os.environ.get('DATABASE_URL'):raise RuntimeError('Usa una base exclusiva para pruebas, distinta de DATABASE_URL.')
    schema='test_'+uuid.uuid4().hex
    with psycopg.connect(url,autocommit=True) as conn:conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    test_url=make_conninfo(url,options='-c search_path='+schema+',public')
    monkeypatch.setenv('CLOUDINARY_URL','cloudinary://test:test@demo');monkeypatch.setenv('APP_ENV','development');monkeypatch.setenv('COOKIE_SECURE','false');monkeypatch.setenv('DB_POOL_MAX','1')
    from infrastructure import make_pool
    def configure(conn):
        conn.execute(sql.SQL('SET search_path TO {},public').format(sql.Identifier(schema)));conn.commit()
    monkeypatch.setattr('main.make_pool',lambda url,config:make_pool(url,config,configure=configure))
    try:
        with TestClient(create_app(db_url=test_url,admin_password=PASSWORD,seed_demo=False)) as c:yield c
    finally:
        with psycopg.connect(url,autocommit=True) as conn:conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))

@pytest.fixture
def headers(client):
    result=client.post('/api/auth/login',json={'usuario':'admin','password':PASSWORD})
    assert result.status_code==200,result.text
    return {'X-CSRF-Token':result.json()['csrf_token']}

def product(**changes):
    return {'nombre':'Mezcal de prueba','precio':'890.50','descripcion':'Descripción de un producto para las pruebas.','imagen_url':'/static/img/bottle.svg','published':True,**changes}
