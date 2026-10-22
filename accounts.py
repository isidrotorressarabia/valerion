"""Basic customer identity and profile management. No commerce endpoints."""
import hashlib
import hmac
import secrets
import time
import re
from datetime import datetime,timezone
from typing import Annotated
from fastapi import Depends,HTTPException,Request,Response,Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel,ConfigDict,Field,field_validator
from infrastructure import database,audit

COOKIE='valerion_customer'
TTL=8*3600

def stamp():return datetime.now(timezone.utc).isoformat()
def digest(password,salt):return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
def password_pair(password):
    salt=secrets.token_hex(16);return salt,digest(password,salt)

class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=False)
class Login(Model):
    email:str=Field(max_length=254)
    password:str=Field(min_length=1,max_length=256)
    @field_validator('email')
    @classmethod
    def email_valid(cls,v):
        v=v.strip().lower()
        if not re.fullmatch(r'[a-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}',v):raise ValueError('Correo inválido.')
        return v
class Register(Login):
    nombre:str=Field(min_length=2,max_length=100)
    password:str=Field(min_length=12,max_length=256)
    @field_validator('nombre')
    @classmethod
    def name_valid(cls,v):
        v=v.strip()
        if len(v)<2 or any(ord(c)<32 for c in v):raise ValueError('Nombre inválido.')
        return v
class Profile(Model):
    nombre:str=Field(min_length=2,max_length=100)
    preferencia_contacto:bool=False
    _name=field_validator('nombre')(Register.name_valid.__func__)
class Password(Model):
    actual:str=Field(min_length=1,max_length=256)
    nueva:str=Field(min_length=12,max_length=256)
class Status(Model):
    activo:bool

def migrate(cursor):
    cursor.execute('''CREATE TABLE IF NOT EXISTS clientes(
      id SERIAL PRIMARY KEY,nombre TEXT NOT NULL,email TEXT NOT NULL UNIQUE,
      password_salt TEXT NOT NULL,password_hash TEXT NOT NULL,activo BOOLEAN NOT NULL DEFAULT TRUE,
      creado_en TEXT NOT NULL,ultimo_acceso TEXT);
      ALTER TABLE clientes ADD COLUMN IF NOT EXISTS preferencia_contacto BOOLEAN NOT NULL DEFAULT FALSE;
      CREATE TABLE IF NOT EXISTS customer_sessions(token_hash TEXT PRIMARY KEY,
      cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,expires INTEGER NOT NULL);
      ALTER TABLE customer_sessions ADD COLUMN IF NOT EXISTS csrf TEXT;
      DELETE FROM customer_sessions WHERE csrf IS NULL;
      CREATE INDEX IF NOT EXISTS customer_sessions_expiry ON customer_sessions(expires);
      CREATE INDEX IF NOT EXISTS customer_sessions_owner ON customer_sessions(cliente_id);
      CREATE TABLE IF NOT EXISTS account_limits(key TEXT PRIMARY KEY,attempts INTEGER NOT NULL,expires BIGINT NOT NULL);
    ''')

def current(request):
    token=request.cookies.get(COOKIE,'')
    if not token or len(token)>256:return None
    with database(request.app) as (_,cur):
        cur.execute('''SELECT c.id,c.nombre,c.email,c.preferencia_contacto,s.csrf,s.token_hash FROM clientes c
            JOIN customer_sessions s ON s.cliente_id=c.id WHERE s.token_hash=%s AND s.expires>%s AND c.activo=TRUE''',
            (hashlib.sha256(token.encode()).hexdigest(),int(time.time())))
        return cur.fetchone()

def customer(request:Request):
    row=current(request)
    if not row:raise HTTPException(401,'Inicia sesión para acceder a tu perfil.')
    return row

def write(request:Request,user:Annotated[dict,Depends(customer)]):
    if not hmac.compare_digest(request.headers.get('x-csrf-token',''),user['csrf']):raise HTTPException(403,'Recarga la página y vuelve a intentar.')
    return user

def throttle(app,key,limit=10):
    key=hashlib.sha256(key.encode()).hexdigest();now=int(time.time())
    with database(app) as (_,cur):
        cur.execute('''INSERT INTO account_limits(key,attempts,expires) VALUES(%s,1,%s)
          ON CONFLICT(key) DO UPDATE SET attempts=CASE WHEN account_limits.expires<=%s THEN 1 ELSE account_limits.attempts+1 END,
          expires=CASE WHEN account_limits.expires<=%s THEN EXCLUDED.expires ELSE account_limits.expires END RETURNING attempts,expires''',(key,now+300,now,now))
        row=cur.fetchone();cur.execute('DELETE FROM account_limits WHERE expires<%s',(now-3600,))
    if row['attempts']>limit:raise HTTPException(429,'Demasiados intentos. Espera unos minutos.',headers={'Retry-After':str(max(1,row['expires']-now))})

def session(cur,app,response,cid):
    token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
    cur.execute('DELETE FROM customer_sessions WHERE cliente_id=%s OR expires<=%s',(cid,int(time.time())))
    cur.execute('INSERT INTO customer_sessions(token_hash,cliente_id,expires,csrf) VALUES(%s,%s,%s,%s)',(hashlib.sha256(token.encode()).hexdigest(),cid,int(time.time())+TTL,csrf))
    response.set_cookie(COOKIE,token,httponly=True,secure=app.state.secure_cookie,samesite='strict',max_age=TTL,path='/')
    return {'csrf_token':csrf}

def attach(app,templates,page_context,require_admin,require_write):
    @app.get('/cuenta',include_in_schema=False)
    def account_page(request:Request):
        if not current(request):return RedirectResponse('/cuenta/login',303)
        return templates.TemplateResponse(request=request,name='account.html',context=page_context())

    @app.get('/cuenta/login',include_in_schema=False)
    @app.get('/cuenta/registro',include_in_schema=False)
    def access_page(request:Request):
        return templates.TemplateResponse(request=request,name='account_access.html',context={**page_context(),'register':request.url.path.endswith('registro')})

    @app.post('/api/clientes/registro',status_code=201)
    def register(body:Register,request:Request,response:Response):
        throttle(app,'register:'+request.client.host,5)
        salt,pw=password_pair(body.password)
        with database(app) as (_,cur):
            cur.execute('''INSERT INTO clientes(nombre,email,password_salt,password_hash,creado_en,ultimo_acceso)
                VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(email) DO NOTHING RETURNING id''',(body.nombre,body.email,salt,pw,stamp(),stamp()))
            row=cur.fetchone()
            if not row:raise HTTPException(409,'No se pudo crear la cuenta con esos datos. Si ya tienes una cuenta, inicia sesión.')
            audit(cur,'account.register',str(row['id']),'customer')
            return session(cur,app,response,row['id'])

    @app.post('/api/clientes/login')
    def login(body:Login,request:Request,response:Response):
        throttle(app,'login:'+request.client.host)
        throttle(app,'email:'+body.email,20)
        with database(app) as (_,cur):
            cur.execute('SELECT * FROM clientes WHERE email=%s FOR UPDATE',(body.email,));row=cur.fetchone()
            candidate=digest(body.password,row['password_salt'] if row else '00'*16)
            if not row or not hmac.compare_digest(candidate,row['password_hash']) or not row['activo']:raise HTTPException(401,'Correo o contraseña incorrectos.')
            cur.execute('UPDATE clientes SET ultimo_acceso=%s WHERE id=%s',(stamp(),row['id']))
            return session(cur,app,response,row['id'])

    @app.get('/api/clientes/me')
    def me(user:Annotated[dict,Depends(customer)]):
        return {k:v for k,v in user.items() if k!='token_hash'}

    @app.put('/api/clientes/perfil')
    def profile(body:Profile,user:Annotated[dict,Depends(write)]):
        with database(app) as (_,cur):
            cur.execute('UPDATE clientes SET nombre=%s,preferencia_contacto=%s WHERE id=%s AND activo=TRUE',(body.nombre,body.preferencia_contacto,user['id']))
            audit(cur,'account.profile',str(user['id']),'customer')
        return {'ok':True}

    @app.post('/api/clientes/password')
    def change_password(body:Password,user:Annotated[dict,Depends(write)]):
        throttle(app,'password:'+str(user['id']),5)
        with database(app) as (_,cur):
            cur.execute('SELECT * FROM clientes WHERE id=%s AND activo=TRUE FOR UPDATE',(user['id'],));row=cur.fetchone()
            if not row or not hmac.compare_digest(digest(body.actual,row['password_salt']),row['password_hash']):raise HTTPException(400,'La contraseña actual no coincide.')
            salt,pw=password_pair(body.nueva)
            cur.execute('UPDATE clientes SET password_salt=%s,password_hash=%s WHERE id=%s',(salt,pw,user['id']))
            cur.execute('DELETE FROM customer_sessions WHERE cliente_id=%s',(user['id'],))
            audit(cur,'account.password',str(user['id']),'customer')
        return {'ok':True,'message':'Contraseña actualizada. Inicia sesión nuevamente.'}

    @app.post('/api/clientes/logout',status_code=204)
    def logout(response:Response,user:Annotated[dict,Depends(write)]):
        with database(app) as (_,cur):cur.execute('DELETE FROM customer_sessions WHERE cliente_id=%s',(user['id'],))
        response.delete_cookie(COOKIE,path='/',secure=app.state.secure_cookie,httponly=True,samesite='strict')

    @app.get('/admin/clientes',include_in_schema=False)
    def customers_page(request:Request,admin:Annotated[dict,Depends(require_admin)]):
        return templates.TemplateResponse(request=request,name='customers.html',context={'username':app.state.username})

    @app.get('/api/admin/clientes')
    def customers(admin:Annotated[dict,Depends(require_admin)],q:str=Query('',max_length=100),page:int=Query(1,ge=1,le=10000)):
        value='%'+q.replace('\\','\\\\').replace('%',r'\%').replace('_',r'\_')+'%'
        with database(app) as (_,cur):
            where=" WHERE nombre ILIKE %s OR email ILIKE %s"
            cur.execute('SELECT count(*) AS total FROM clientes'+where,(value,value));total=cur.fetchone()['total']
            cur.execute('SELECT id,nombre,email,activo,preferencia_contacto,creado_en,ultimo_acceso FROM clientes'+where+' ORDER BY id DESC LIMIT 20 OFFSET %s',(value,value,(page-1)*20))
            return {'items':cur.fetchall(),'total':total,'page':page,'pages':max(1,(total+19)//20)}

    @app.patch('/api/admin/clientes/{cid}')
    def status(cid:int,body:Status,admin:Annotated[dict,Depends(require_write)]):
        with database(app) as (_,cur):
            cur.execute('UPDATE clientes SET activo=%s WHERE id=%s RETURNING id',(body.activo,cid))
            if not cur.fetchone():raise HTTPException(404,'Cuenta no encontrada.')
            cur.execute('DELETE FROM customer_sessions WHERE cliente_id=%s',(cid,))
            audit(cur,'account.enable' if body.activo else 'account.disable',str(cid),app.state.username)
        return {'ok':True}
