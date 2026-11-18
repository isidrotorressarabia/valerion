"""Customer identity and authenticated profile management."""
import hashlib
import hmac
import secrets
import time
import re
from datetime import datetime,timezone
from typing import Annotated
from fastapi import BackgroundTasks,Depends,HTTPException,Request,Response,Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse,RedirectResponse
from pydantic import BaseModel,ConfigDict,Field,field_validator
from infrastructure import database,audit
from services.crm import DeliveryUpdate, Channel, save_profile
from services.logistics import event
from services import mail
from services.legal import LEGAL_VERSION

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
    acepto_aviso:bool|str|None=None
    mayor_edad:bool|str|None=None
    @field_validator('nombre')
    @classmethod
    def name_valid(cls,v):
        v=v.strip()
        if len(v)<2 or any(ord(c)<32 for c in v):raise ValueError('Nombre inválido.')
        return v
class Profile(DeliveryUpdate):
    canal_preferido: Channel | None = None
    nombre:str=Field(min_length=2,max_length=100)
    preferencia_contacto:bool=False
    _name=field_validator('nombre')(Register.name_valid.__func__)
class Password(Model):
    actual:str=Field(min_length=1,max_length=256)
    nueva:str=Field(min_length=12,max_length=256)
class Status(Model):
    activo:bool
class EmailOnly(Model):
    email:str=Field(max_length=254)
    _email=field_validator('email')(Login.email_valid.__func__)
class TokenOnly(Model):
    token:str=Field(min_length=10,max_length=128)
class Reset(TokenOnly):
    nueva:str=Field(min_length=12,max_length=256)
class Erase(Model):
    password:str=Field(min_length=1,max_length=256)

def truthy(value):return value is True or (isinstance(value,str) and value.lower() in {'on','true','1'})
def client_ip(request):return request.client.host if request.client else 'local'
def issue_token(cur,cid,purpose,ttl):
    now=int(time.time())
    cur.execute('DELETE FROM cliente_tokens WHERE expires<=%s OR (cliente_id=%s AND proposito=%s)',(now,cid,purpose))
    raw=secrets.token_urlsafe(32)
    cur.execute('INSERT INTO cliente_tokens(token_hash,cliente_id,proposito,expires) VALUES(%s,%s,%s,%s)',(hashlib.sha256(raw.encode()).hexdigest(),cid,purpose,now+ttl))
    return raw
def consume_token(cur,raw,purpose):
    cur.execute('DELETE FROM cliente_tokens WHERE token_hash=%s AND proposito=%s AND expires>%s RETURNING cliente_id',(hashlib.sha256(raw.encode()).hexdigest(),purpose,int(time.time())))
    row=cur.fetchone();return row['cliente_id'] if row else None

def migrate(cursor):
    cursor.execute("SELECT 1 FROM information_schema.columns WHERE table_name='clientes' AND column_name='email_verificado_en'")
    first_verification=cursor.fetchone() is None
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
    cursor.execute('''ALTER TABLE clientes ADD COLUMN IF NOT EXISTS email_verificado_en TIMESTAMPTZ;
      ALTER TABLE clientes ADD COLUMN IF NOT EXISTS aceptacion_legal_en TIMESTAMPTZ;
      ALTER TABLE clientes ADD COLUMN IF NOT EXISTS version_legal TEXT;
      ALTER TABLE clientes ADD COLUMN IF NOT EXISTS mayor_edad_declarada BOOLEAN NOT NULL DEFAULT FALSE;
      ALTER TABLE clientes ADD COLUMN IF NOT EXISTS eliminado_en TIMESTAMPTZ;
      CREATE TABLE IF NOT EXISTS cliente_tokens(token_hash TEXT PRIMARY KEY,
        cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,proposito TEXT NOT NULL,expires BIGINT NOT NULL);
      CREATE INDEX IF NOT EXISTS cliente_tokens_owner ON cliente_tokens(cliente_id,proposito);''')
    if first_verification:  # cuentas previas a la verificación: se consideran verificadas
        cursor.execute('UPDATE clientes SET email_verificado_en=now() WHERE email_verificado_en IS NULL')

def current(request):
    token=request.cookies.get(COOKIE,'')
    if not token or len(token)>256:return None
    with database(request.app) as (_,cur):
        cur.execute('''SELECT c.id,c.nombre,c.email,c.preferencia_contacto,c.canal_preferido,c.entrega_segura,(c.email_verificado_en IS NOT NULL) AS email_verificado,s.csrf,s.token_hash FROM clientes c
            JOIN customer_sessions s ON s.cliente_id=c.id WHERE s.token_hash=%s AND s.expires>%s AND c.activo=TRUE''',
            (hashlib.sha256(token.encode()).hexdigest(),int(time.time())))
        row = cur.fetchone()
    if row:
        row.update(request.app.state.vault.open(f"clientes:{row['id']}", row.pop("entrega_segura")))
    return row

def customer(request:Request):
    row=current(request)
    if not row:raise HTTPException(401,'Inicia sesión para acceder a tu perfil.')
    return row

def write(request:Request,user:Annotated[dict,Depends(customer)]):
    if not hmac.compare_digest(request.headers.get('x-csrf-token','').encode(),user['csrf'].encode()):raise HTTPException(403,'Recarga la página y vuelve a intentar.')
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
    response.set_cookie(COOKIE,token,httponly=True,secure=app.state.secure_cookie,samesite='lax',max_age=TTL,path='/')
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

    def send_link(email,nombre,token,path,subject,text):
        mail.send_plain(app,email,subject,f"Hola {nombre},\n\n{text}\n\n{app.state.settings.origin}{path}#t={token}\n\nSi no fuiste tú, ignora este mensaje.\n\nValerion")

    @app.post('/api/clientes/registro',status_code=201)
    def register(body:Register,request:Request,response:Response,background_tasks:BackgroundTasks):
        throttle(app,'register:'+client_ip(request),10)
        consent=truthy(body.acepto_aviso) and truthy(body.mayor_edad)
        if app.state.settings.consent_required and not consent:
            raise HTTPException(422,'Debes aceptar el Aviso de Privacidad y declarar que eres mayor de 18 años.')
        salt,pw=password_pair(body.password)
        verify=None
        with database(app) as (_,cur):
            cur.execute('''INSERT INTO clientes(nombre,email,password_salt,password_hash,creado_en,ultimo_acceso,aceptacion_legal_en,version_legal,mayor_edad_declarada)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(email) DO NOTHING RETURNING id''',(body.nombre,body.email,salt,pw,stamp(),stamp(),datetime.now(timezone.utc) if consent else None,LEGAL_VERSION if consent else None,consent))
            row=cur.fetchone()
            if not row:raise HTTPException(409,'No se pudo crear la cuenta con esos datos. Si ya tienes una cuenta, inicia sesión.')
            event(cur,row['id'],None,'cuenta.creada',f"cliente:{row['id']}")
            audit(cur,'account.register',str(row['id']),'customer')
            if mail.configured(app):verify=issue_token(cur,row['id'],'verificar',3*86400)
            result=session(cur,app,response,row['id'])
        if verify:background_tasks.add_task(send_link,body.email,body.nombre,verify,'/cuenta/verificar','Verifica tu correo para poder comprar. El enlace vence en 3 días:','Valerion · Verifica tu correo')
        return result

    @app.post('/api/clientes/recuperar',status_code=202)
    def recover(body:EmailOnly,request:Request,background_tasks:BackgroundTasks):
        if not mail.configured(app):raise HTTPException(503,'La recuperación por correo no está disponible. Contacta a la tienda.')
        throttle(app,'recover:'+client_ip(request),10);throttle(app,'recover-email:'+body.email,3)
        token=None
        with database(app) as (_,cur):
            cur.execute('SELECT id,nombre FROM clientes WHERE email=%s AND activo=TRUE',(body.email,));row=cur.fetchone()
            if row:token=issue_token(cur,row['id'],'recuperar',1800)
        if token:background_tasks.add_task(send_link,body.email,row['nombre'],token,'/cuenta/restablecer','Recibimos una solicitud para restablecer tu contraseña. El enlace vence en 30 minutos:','Valerion · Restablece tu contraseña')
        return {'ok':True,'message':'Si el correo está registrado, recibirás instrucciones en unos minutos.'}

    @app.post('/api/clientes/restablecer')
    def reset(body:Reset,request:Request):
        throttle(app,'reset:'+client_ip(request),10)
        with database(app) as (_,cur):
            cid=consume_token(cur,body.token,'recuperar')
            cur.execute('SELECT id FROM clientes WHERE id=%s AND activo=TRUE FOR UPDATE',(cid or 0,))
            if not cid or not cur.fetchone():raise HTTPException(400,'El enlace no es válido o ya expiró. Solicita uno nuevo.')
            salt,pw=password_pair(body.nueva)
            cur.execute('UPDATE clientes SET password_salt=%s,password_hash=%s,email_verificado_en=COALESCE(email_verificado_en,now()) WHERE id=%s',(salt,pw,cid))
            cur.execute('DELETE FROM customer_sessions WHERE cliente_id=%s',(cid,))
            cur.execute("DELETE FROM cliente_tokens WHERE cliente_id=%s AND proposito='recuperar'",(cid,))
            event(cur,cid,None,'cuenta.password_restablecida',f'cliente:{cid}')
            audit(cur,'account.password_reset',str(cid),'customer')
        return {'ok':True,'message':'Contraseña actualizada. Ya puedes iniciar sesión.'}

    @app.post('/api/clientes/verificar')
    def verify_email(body:TokenOnly,request:Request):
        throttle(app,'verify:'+client_ip(request),20)
        with database(app) as (_,cur):
            cid=consume_token(cur,body.token,'verificar')
            if not cid:raise HTTPException(400,'El enlace no es válido o ya expiró. Pide uno nuevo desde Mi cuenta.')
            cur.execute('UPDATE clientes SET email_verificado_en=COALESCE(email_verificado_en,now()) WHERE id=%s',(cid,))
            event(cur,cid,None,'cuenta.correo_verificado',f'cliente:{cid}')
        return {'ok':True,'message':'Correo verificado. Ya puedes comprar.'}

    @app.post('/api/clientes/verificacion/reenviar',status_code=202)
    def resend_verification(user:Annotated[dict,Depends(write)],background_tasks:BackgroundTasks):
        if user.get('email_verificado'):return {'ok':True,'message':'Tu correo ya está verificado.'}
        if not mail.configured(app):raise HTTPException(503,'El envío de correos no está disponible.')
        throttle(app,'resend:'+str(user['id']),3)
        with database(app) as (_,cur):token=issue_token(cur,user['id'],'verificar',3*86400)
        background_tasks.add_task(send_link,user['email'],user['nombre'],token,'/cuenta/verificar','Verifica tu correo para poder comprar. El enlace vence en 3 días:','Valerion · Verifica tu correo')
        return {'ok':True,'message':'Te enviamos un nuevo enlace.'}

    @app.get('/api/clientes/me/datos')
    def export_data(user:Annotated[dict,Depends(customer)]):
        with database(app) as (_,cur):
            cur.execute('SELECT nombre,email,creado_en,ultimo_acceso,preferencia_contacto,canal_preferido,aceptacion_legal_en,version_legal,mayor_edad_declarada,email_verificado_en FROM clientes WHERE id=%s',(user['id'],));profile=cur.fetchone()
            cur.execute('SELECT id,producto_nombre,monto_centavos,estado,creado_en,actualizado_en,entrega_segura FROM ordenes WHERE cliente_id=%s ORDER BY id',(user['id'],));orders=cur.fetchall()
        for order in orders:
            sealed=order.pop('entrega_segura');order['entrega']=app.state.vault.open(f"ordenes:{order['id']}",sealed) if sealed else {}
        delivery={k:user.get(k) for k in ('direccion','cp','ciudad','telefono','latitud','longitud')}
        return JSONResponse(jsonable_encoder({'perfil':profile,'entrega':delivery,'pedidos':orders}),headers={'Content-Disposition':'attachment; filename="mis-datos-valerion.json"'})

    @app.delete('/api/clientes/me')
    def erase_account(body:Erase,response:Response,user:Annotated[dict,Depends(write)]):
        throttle(app,'erase:'+str(user['id']),5)
        with database(app) as (_,cur):
            cur.execute('SELECT * FROM clientes WHERE id=%s AND activo=TRUE FOR UPDATE',(user['id'],));row=cur.fetchone()
            if not row or not hmac.compare_digest(digest(body.password,row['password_salt']),row['password_hash']):raise HTTPException(400,'La contraseña no coincide.')
            cur.execute("SELECT count(*) AS n FROM ordenes WHERE cliente_id=%s AND estado IN ('pagada','en_preparacion','enviada')",(user['id'],))
            if cur.fetchone()['n']:raise HTTPException(409,'Tienes pedidos en proceso. Podrás eliminar tu cuenta cuando se entreguen o se cancelen.')
            cur.execute("UPDATE ordenes SET estado='cancelada',actualizado_en=%s WHERE cliente_id=%s AND estado='pendiente'",(stamp(),user['id']))
            cur.execute('SELECT id FROM ordenes WHERE cliente_id=%s',(user['id'],))
            for order in cur.fetchall():cur.execute('UPDATE ordenes SET entrega_segura=%s WHERE id=%s',(app.state.vault.seal(f"ordenes:{order['id']}",{}),order['id']))
            salt,pw=password_pair(secrets.token_urlsafe(32))
            cur.execute("UPDATE clientes SET nombre='Cuenta eliminada',email=%s,activo=FALSE,password_salt=%s,password_hash=%s,entrega_segura=%s,preferencia_contacto=FALSE,eliminado_en=now() WHERE id=%s",(f"eliminado-{user['id']}@invalid.local",salt,pw,app.state.vault.seal(f"clientes:{user['id']}",{}),user['id']))
            cur.execute('DELETE FROM customer_sessions WHERE cliente_id=%s',(user['id'],))
            cur.execute('DELETE FROM cliente_tokens WHERE cliente_id=%s',(user['id'],))
            event(cur,user['id'],None,'cuenta.eliminada',f"cliente:{user['id']}")
            audit(cur,'account.erase',str(user['id']),'customer')
        response.delete_cookie(COOKIE,path='/',secure=app.state.secure_cookie,httponly=True,samesite='lax')
        return {'ok':True,'message':'Tu cuenta fue eliminada y tus datos personales anonimizados.'}

    def extra_page(path,mode,private=False):
        def view(request:Request):
            if private and not current(request):return RedirectResponse('/cuenta/login',303)
            return templates.TemplateResponse(request=request,name='account_extra.html',context={**page_context(),'mode':mode})
        app.get(path,include_in_schema=False)(view)
    for _path,_mode,_private in (('/cuenta/recuperar','recuperar',False),('/cuenta/restablecer','restablecer',False),('/cuenta/verificar','verificar',False),('/cuenta/privacidad','privacidad',True)):
        extra_page(_path,_mode,_private)

    @app.post('/api/clientes/login')
    def login(body:Login,request:Request,response:Response):
        throttle(app,'login:'+(request.client.host if request.client else 'local'),40)
        throttle(app,'email:'+body.email,20)
        with database(app) as (_,cur):
            cur.execute('SELECT * FROM clientes WHERE email=%s FOR UPDATE',(body.email,));row=cur.fetchone()
            candidate=digest(body.password,row['password_salt'] if row else '00'*16)
            if not row or not hmac.compare_digest(candidate,row['password_hash']) or not row['activo']:raise HTTPException(401,'Correo o contraseña incorrectos.')
            cur.execute('UPDATE clientes SET ultimo_acceso=%s WHERE id=%s',(stamp(),row['id']))
            return session(cur,app,response,row['id'])

    @app.get('/api/clientes/me')
    def me(user:Annotated[dict,Depends(customer)]):
        return {**{k:v for k,v in user.items() if k!='token_hash'},'verificacion_requerida':mail.configured(app)}

    @app.put('/api/clientes/perfil')
    def profile(body:Profile,user:Annotated[dict,Depends(write)]):
        save_profile(app, user['id'], body)
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
        response.delete_cookie(COOKIE,path='/',secure=app.state.secure_cookie,httponly=True,samesite='lax')

    @app.get('/admin/clientes',include_in_schema=False)
    def customers_page(request:Request,admin:Annotated[dict,Depends(require_admin)]):
        return templates.TemplateResponse(request=request,name='customers.html',context={'username':app.state.username})

    @app.get('/api/admin/clientes')
    def customers(admin:Annotated[dict,Depends(require_admin)],q:str=Query('',max_length=100),page:int=Query(1,ge=1,le=10000)):
        value='%'+q.replace('\\','\\\\').replace('%',r'\%').replace('_',r'\_')+'%'
        with database(app) as (_,cur):
            where=" WHERE nombre ILIKE %s OR email ILIKE %s"
            cur.execute('SELECT count(*) AS total FROM clientes'+where,(value,value));total=cur.fetchone()['total']
            cur.execute('SELECT id,nombre,email,activo,preferencia_contacto,canal_preferido,creado_en,ultimo_acceso FROM clientes'+where+' ORDER BY id DESC LIMIT 20 OFFSET %s',(value,value,(page-1)*20))
            return {'items':cur.fetchall(),'total':total,'page':page,'pages':max(1,(total+19)//20)}

    @app.patch('/api/admin/clientes/{cid}')
    def status(cid:int,body:Status,admin:Annotated[dict,Depends(require_write)]):
        with database(app) as (_,cur):
            cur.execute('UPDATE clientes SET activo=%s WHERE id=%s RETURNING id',(body.activo,cid))
            if not cur.fetchone():raise HTTPException(404,'Cuenta no encontrada.')
            cur.execute('DELETE FROM customer_sessions WHERE cliente_id=%s',(cid,))
            audit(cur,'account.enable' if body.activo else 'account.disable',str(cid),app.state.username)
        return {'ok':True}
