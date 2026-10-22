"""Valerion: catálogo con FastAPI, Jinja2, PostgreSQL (Neon), Cloudinary y sesiones revocables."""
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import tempfile
import zipfile
from starlette.background import BackgroundTask
from io import BytesIO
import os
import re
from pathlib import Path
import secrets
import time
import warnings
from typing import Annotated, Literal
from urllib.parse import urlsplit

import logging
import uuid
import psycopg
from psycopg_pool import PoolTimeout,TooManyRequests
from psycopg.conninfo import conninfo_to_dict
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.gzip import GZipMiddleware
from infrastructure import database,make_pool,TTLCache,catalog_page,categories,admin_metrics,audit
from migrations import upgrade
from accounts import migrate as migrate_accounts,attach as attach_accounts
from media_store import cloud_settings,store_upload,optimized_url,usage,delete_unused,referenced_urls
from backups import create_backup
log=logging.getLogger('valerion')
from dotenv import dotenv_values
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, File, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_validator
from PIL import Image, ImageOps, UnidentifiedImageError

import cloudinary
import cloudinary.uploader

from configurar import ROOT, ensure_config
from content import CONTENT_FIELDS, DEFAULT_CONTENT, FIELD_MAP, LEGACY_REPLACEMENTS
from cms import Studio, StudioInput, PublishInput, ProductExtra, theme_css, whatsapp_link

MAX_UPLOAD = 8 * 1024 * 1024
MEDIA_NAME = re.compile(r"^[a-f0-9]{32}\.webp$")
STATIC_IMAGES = {"/static/img/" + name for name in ("emblem.svg", "hero.svg", "bottle.svg", "placeholder.svg", "agave-reference.jpg")}

def image_reference(value: str) -> str:
    if value in STATIC_IMAGES or (value.startswith("/media/") and MEDIA_NAME.fullmatch(value[7:])):
        return value
    url = TypeAdapter(HttpUrl).validate_python(value)
    if url.scheme != "https" or url.username or url.password:
        raise ValueError("Usa HTTPS o una imagen cargada desde el panel.")
    return str(url)

class ContentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valores: dict[str, str] = Field(min_length=1)

    @field_validator("valores")
    @classmethod
    def validate_content(cls, values):
        cleaned = {}
        for key, value in values.items():
            spec = FIELD_MAP.get(key)
            if not spec:
                raise ValueError("Campo de contenido desconocido.")
            value = value.strip()
            if (not value and not spec["optional"]) or len(value) > 4000:
                raise ValueError(f"{spec['label']}: revisa longitud y contenido.")
            cleaned[key] = image_reference(value) if spec["type"] == "image" else value
        return cleaned

COOKIE = "valerion_session"
SESSION_SECONDS = 8 * 60 * 60
AGAVE_IMAGE = "https://images.unsplash.com/photo-1732043846982-5522e1e2626c?auto=format&fit=crop&w=1000&q=85"
DEMO_PRODUCTS = [
    {"nombre": "Valerion Espadín", "precio": "890.00", "descripcion": "Propuesta de colección: una expresión de entrada para descubrir el universo Valerion.", "link_venta": None, "imagen_url": AGAVE_IMAGE},
    {"nombre": "Valerion Tobalá", "precio": "1490.00", "descripcion": "Propuesta de colección: una expresión para quienes disfrutan explorar nuevos matices.", "link_venta": None, "imagen_url": AGAVE_IMAGE},
    {"nombre": "Valerion Ensamble", "precio": "1890.00", "descripcion": "Propuesta de colección: un encuentro de agaves con identidad propia.", "link_venta": None, "imagen_url": AGAVE_IMAGE},
]

class ProductInput(ProductExtra):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    nombre: str = Field(min_length=2, max_length=100)
    precio: Decimal = Field(gt=0, le=Decimal("999999.99"), max_digits=8, decimal_places=2)
    descripcion: str = Field(min_length=10, max_length=2000)
    link_venta: HttpUrl | None = None
    imagen_url: str = Field(max_length=2083)

    @field_validator("link_venta", mode="before")
    @classmethod
    def empty_link(cls, value):
        return None if value == "" else value

    @field_validator("imagen_url")
    @classmethod
    def image_url(cls, value):
        return image_reference(value)

    @field_validator("link_venta")
    @classmethod
    def secure_url(cls, value):
        if value is not None and (value.scheme != "https" or value.username or value.password):
            raise ValueError("Usa una URL HTTPS sin credenciales.")
        return value

class Product(ProductInput):
    id: int
    creado_en: str
    actualizado_en: str

class Login(BaseModel):
    usuario: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")

def public_product(row: dict) -> dict:
    return {
        "id": row["id"], "nombre": row["nombre"],
        "precio": f"{Decimal(row['precio_centavos']) / 100:.2f}",
        "descripcion": row["descripcion"], "link_venta": row["link_venta"],
        "imagen_url": row["imagen_url"], "creado_en": row["creado_en"],
        "actualizado_en": row["actualizado_en"],
        **ProductExtra.model_validate_json(row["extra_json"]).model_dump(mode="json"),
    }

def values(product: ProductInput) -> tuple:
    return (product.nombre, int(product.precio * 100), product.descripcion,
            str(product.link_venta) if product.link_venta else None, str(product.imagen_url))

def insert_product(cursor, product: ProductInput) -> int:
    timestamp = now_iso()
    cursor.execute(
        "INSERT INTO mezcales(nombre,precio_centavos,descripcion,link_venta,imagen_url,creado_en,actualizado_en,extra_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (*values(product), timestamp, timestamp, extra_json(product)),
    )
    return cursor.fetchone()["id"]

def extra_json(product):
    return json.dumps({k: product.model_dump(mode="json")[k] for k in ProductExtra.model_fields}, ensure_ascii=False)

def initialize(app):
    with database(app,write=True) as (conn, cursor):
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS mezcales (
            id SERIAL PRIMARY KEY,
            nombre TEXT NOT NULL,
            precio_centavos INTEGER NOT NULL CHECK(precio_centavos > 0),
            descripcion TEXT NOT NULL,
            link_venta TEXT,
            imagen_url TEXT NOT NULL,
            creado_en TEXT NOT NULL,
            actualizado_en TEXT NOT NULL,
            extra_json TEXT NOT NULL DEFAULT '{}'
        );
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS site_content (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            csrf TEXT NOT NULL,
            expires INTEGER NOT NULL,
            credential_fingerprint TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS login_attempts (
            client TEXT PRIMARY KEY, failures INTEGER NOT NULL, last_attempt INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS studio (id INTEGER PRIMARY KEY CHECK(id=1), draft TEXT NOT NULL, published TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, published_at TEXT);
        CREATE TABLE IF NOT EXISTS studio_history (id SERIAL PRIMARY KEY, document TEXT NOT NULL, published_at TEXT NOT NULL);
        """)
        
        upgrade(cursor,conn)
        migrate_accounts(cursor)
        initial = Studio().model_dump_json()
        cursor.execute('INSERT INTO studio(id,draft,published,version) VALUES(1,%s,%s,1) ON CONFLICT (id) DO NOTHING', (initial,initial))
        
        for k, v in DEFAULT_CONTENT.items():
            cursor.execute("INSERT INTO site_content(key,value) VALUES(%s,%s) ON CONFLICT (key) DO NOTHING", (k, v))
            
        cursor.execute("SELECT 1 FROM metadata WHERE key='initialized'")
        if not cursor.fetchone():
            if app.state.seed_demo:
                for item in DEMO_PRODUCTS:
                    insert_product(cursor, ProductInput(**item))
            cursor.execute("INSERT INTO metadata VALUES('initialized','1')")
            
        cursor.execute("SELECT 1 FROM metadata WHERE key='v2_content'")
        if not cursor.fetchone():
            for key, (old, new) in LEGACY_REPLACEMENTS.items():
                cursor.execute('UPDATE site_content SET value=%s WHERE key=%s AND value=%s', (new,key,old))
            for item in DEMO_PRODUCTS:
                cursor.execute("UPDATE mezcales SET extra_json=%s WHERE nombre=%s AND descripcion=%s AND precio_centavos=%s AND imagen_url=%s AND link_venta IS NULL", ('{"published":false}',item['nombre'],item['descripcion'],int(Decimal(item['precio'])*100),item['imagen_url']))
                cursor.execute("UPDATE mezcales SET descripcion=%s WHERE nombre=%s AND descripcion=%s", (f"{item['nombre']}. Explora esta expresión de nuestra colección.",item['nombre'],item['descripcion']))
            cursor.execute("INSERT INTO metadata VALUES('v2_content','1')")
            
        cursor.execute("DELETE FROM sessions WHERE expires <= %s", (int(time.time()),))
        cursor.execute("DELETE FROM login_attempts WHERE last_attempt < %s", (int(time.time()) - 300,))

def current_session(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token or len(token) > 256:
        return None
    hashed = hashlib.sha256(token.encode()).hexdigest()
    with database(request.app) as (conn, cursor):
        cursor.execute(
            "SELECT * FROM sessions WHERE token_hash=%s AND expires>%s AND credential_fingerprint=%s",
            (hashed, int(time.time()), request.app.state.fingerprint),
        )
        row = cursor.fetchone()
    return dict(row) if row else None

def require_admin(request: Request) -> dict:
    session = current_session(request)
    if not session:
        raise HTTPException(401, "Inicia sesión para administrar el catálogo.")
    return session

def require_write(request: Request, session: Annotated[dict, Depends(require_admin)]) -> dict:
    if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
        raise HTTPException(403, "Token de seguridad inválido. Recarga el panel.")
    return session

def read_content(app):
    def load():
        with database(app) as (_,cursor):
            cursor.execute('SELECT * FROM site_content')
            return {**DEFAULT_CONTENT, **{r['key']:r['value'] for r in cursor.fetchall()}}
    return app.state.content_cache.get('content',load,ttl=120)

def ensure_image_exists(app, reference):
    if reference.startswith("/media/") and not (app.state.upload_dir / reference[7:]).is_file():
        raise HTTPException(422, "La imagen cargada no existe. Vuelve a subirla desde tu equipo.")

def create_app(db_url: str | None = None, admin_password: str | None = None,
               admin_username: str | None = None, seed_demo: bool | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        path=ROOT/'.env'
        config={**dotenv_values(path),**os.environ}
        production=config.get('APP_ENV')=='production'
        password=admin_password if admin_password is not None else config.get('ADMIN_PASSWORD','')
        if not password or not 12<=len(password)<=256:
            raise RuntimeError('Configura ADMIN_PASSWORD con entre 12 y 256 caracteres.')
        app.state.username=admin_username or config.get('ADMIN_USERNAME','admin')
        app.state.secure_cookie=production or str(config.get('COOKIE_SECURE','false')).lower()=='true'
        app.state.seed_demo=seed_demo if seed_demo is not None else str(config.get('SEED_DEMO','false')).lower()=='true'
        url=db_url or config.get('DATABASE_URL')
        if not url:raise RuntimeError('Configura DATABASE_URL con PostgreSQL/Neon.')
        try:info=conninfo_to_dict(url)
        except Exception:raise RuntimeError('DATABASE_URL tiene un formato inválido.') from None
        if production and info.get('sslmode') not in {'require','verify-ca','verify-full'}:
            raise RuntimeError('La conexión de producción necesita sslmode=require o verificación TLS.')
        if not 1<=int(config.get('DB_POOL_MAX',5))<=10:raise RuntimeError('DB_POOL_MAX debe estar entre 1 y 10.')
        try:app.state.cloud=cloud_settings(config.get('CLOUDINARY_URL'))
        except ValueError:raise RuntimeError('Configura CLOUDINARY_URL antes de iniciar.') from None
        app.state.cloud_uploader=cloudinary.uploader.upload
        app.state.cloud_destroy=cloudinary.uploader.destroy
        app.state.media_folder=config.get('CLOUDINARY_FOLDER','valerion_catalogo')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',app.state.media_folder):raise RuntimeError('CLOUDINARY_FOLDER inválido.')
        app.state.content_cache=TTLCache(16);app.state.catalog_cache=TTLCache(128)
        app.state.pool=make_pool(url,config)
        app.state.upload_dir = ROOT / "data" / "uploads"
        app.state.upload_dir.mkdir(parents=True, exist_ok=True)
        app.state.salt = secrets.token_bytes(16)
        app.state.password_hash = hashlib.scrypt(password.encode(), salt=app.state.salt, n=16384, r=8, p=1)
        app.state.fingerprint = hashlib.sha256((app.state.username + "\0" + password).encode()).hexdigest()
        app.state.pool.open()
        try:
            initialize(app)
            yield
        except (psycopg.Error,PoolTimeout):
            raise RuntimeError('No se pudo preparar PostgreSQL. Revisa conectividad, TLS y permisos de migración.') from None
        finally:
            app.state.pool.close()

    app = FastAPI(title="Valerion · API de catálogo y contenido", version="3.1.0", lifespan=lifespan)
    app.add_middleware(GZipMiddleware,minimum_size=1200)
    hosts=str({**dotenv_values(ROOT/'.env'),**os.environ}.get('ALLOWED_HOSTS','')).split(',')
    if any(hosts):app.add_middleware(TrustedHostMiddleware,allowed_hosts=[h.strip() for h in hosts if h.strip()])
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates", context_processors=[lambda request: {"c": read_content(request.app)}])
    
    def read_studio(draft=False):
        def load():
            with database(app) as (_,cursor):
                cursor.execute('SELECT * FROM studio WHERE id=1');row=cursor.fetchone()
            return {'document':json.loads(row['draft' if draft else 'published']),'version':row['version'],'published_at':row['published_at']}
        return load() if draft else app.state.content_cache.get('studio',load,ttl=120)

    def page_context(preview=False):
        studio=read_studio(preview)['document']
        contact=studio['contact']
        return {'studio':studio, 'contact':contact, 'sections':[s for s in studio['sections'] if s['enabled']], 'whatsapp':whatsapp_link(contact), 'has_contact':bool(contact['emails'] or contact['phones'] or contact['whatsapp'] or contact['address'] or contact['socials']), 'preview':preview, 'year':datetime.now().year}

    templates.env.filters['image_size']=optimized_url
    templates.env.filters["mxn"] = lambda value: f"${Decimal(str(value)):,.2f}"

    @app.middleware('http')
    async def security_headers(request:Request,call_next):
        started=time.monotonic();request_id=uuid.uuid4().hex
        request.state.request_id=request_id
        if request.method in {'POST','PUT','PATCH','DELETE'}:
            origin=request.headers.get('origin');expected=urlsplit(str(request.base_url));supplied=urlsplit(origin) if origin else None
            if supplied and (supplied.scheme,supplied.netloc)!=(expected.scheme,expected.netloc) or request.headers.get('sec-fetch-site')=='cross-site':
                return JSONResponse({'detail':'Origen no permitido.'},status_code=403)
            if request.method in {'POST','PUT','PATCH'} and request.url.path.startswith('/api/'):
                content_type='multipart/form-data' if request.url.path=='/api/imagenes' else 'application/json'
                if request.headers.get('content-type','').split(';')[0].strip()!=content_type:
                    return JSONResponse({'detail':'Tipo de contenido no permitido.'},status_code=415)
                try:length=int(request.headers.get('content-length','0'))
                except ValueError:return JSONResponse({'detail':'Longitud inválida.'},status_code=400)
                limit=MAX_UPLOAD+65536 if content_type=='multipart/form-data' else 2*1024*1024
                if length>limit:return JSONResponse({'detail':'La solicitud supera el tamaño permitido.'},status_code=413)
        try:response=await call_next(request)
        except Exception as exc:
            log.error(json.dumps({'event':'request_error','request_id':request_id,'type':type(exc).__name__}))
            response=JSONResponse({'detail':'No se pudo completar la operación. Reintenta más tarde.','request_id':request_id},status_code=503 if isinstance(exc,(psycopg.Error,PoolTimeout,TooManyRequests)) else 500)
        response.headers['X-Request-ID']=request_id
        response.headers['X-Content-Type-Options']='nosniff';response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
        response.headers['Permissions-Policy']='camera=(),microphone=(),geolocation=()'
        if request.url.path not in {'/docs','/redoc','/docs/oauth2-redirect'}:
            response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: blob:; connect-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; object-src 'none'"
        if request.url.path.startswith('/static/'):
            response.headers['Cache-Control']='public,max-age=3600'
        else:response.headers['Cache-Control']='no-store'
        if request.url.scheme=='https':response.headers['Strict-Transport-Security']='max-age=31536000'
        route=getattr(request.scope.get('route'),'path','unmatched')
        log.info(json.dumps({'event':'request','id':request_id,'method':request.method,'route':route,'status':response.status_code,'ms':round((time.monotonic()-started)*1000,1)}))
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        errors = [{"campo": str(e["loc"][-1]), "mensaje": e["msg"]} for e in exc.errors()]
        return JSONResponse({"detail": "Revisa los campos indicados.", "errores": errors}, status_code=422)

    def query_products(query='',sort='destacados',include_drafts=False,page=1,page_size=12,category='',exclude=None):
        return catalog_page(app,public_product,query,sort,include_drafts,page,page_size,category,exclude)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={"products": query_products()["items"], "catalog":query_products(), "categories":categories(app), **page_context()})

    @app.get('/admin/preview', response_class=HTMLResponse, include_in_schema=False)
    def preview_page(request: Request, session: Annotated[dict, Depends(require_admin)]):
        return templates.TemplateResponse(request=request, name='index.html', context={'products':query_products()['items'],'catalog':query_products(),'categories':categories(app), **page_context(True)})

    @app.get('/theme.css', include_in_schema=False)
    def dynamic_theme(request: Request, draft: bool=False):
        if draft:require_admin(request)
        return Response(theme_css(read_studio(draft)['document']['theme']), media_type='text/css')

    @app.get('/producto/{product_id}', response_class=HTMLResponse, include_in_schema=False)
    def product_page(product_id: int, request: Request):
        with database(app) as (conn, cursor):
            cursor.execute('SELECT * FROM mezcales WHERE id=%s', (product_id,))
            row = cursor.fetchone()
        if not row:
            raise HTTPException(404, 'Producto no encontrado.')
        product = public_product(row)
        if not product['published']:
            raise HTTPException(404, 'Producto no encontrado.')
        ctx = page_context()
        ctx['product_whatsapp'] = whatsapp_link(ctx['contact'], f"Hola, me interesa {product['nombre']}. ¿Podrían darme más información?")
        ctx['related'] = query_products(page_size=3,exclude=product_id)['items']
        return templates.TemplateResponse(request=request, name='product.html', context={'product': product, **ctx})

    @app.get('/admin/estudio', response_class=HTMLResponse, include_in_schema=False)
    def studio_page(request: Request):
        if not current_session(request):
            return RedirectResponse('/admin/login',status_code=303)
        return templates.TemplateResponse(request=request,name='studio.html',context={'username':app.state.username})

    @app.get('/api/admin/estudio', tags=['Editor'])
    def get_studio(session: Annotated[dict, Depends(require_admin)]):
        return read_studio(True)

    def validate_reference(cursor,reference):
        ensure_image_exists(app,reference)
        cursor.execute('SELECT deleted_at FROM media_assets WHERE url=%s',(reference,));row=cursor.fetchone()
        if row and row['deleted_at'] is not None:raise HTTPException(422,'La imagen fue eliminada. Vuelve a cargarla.')

    def validate_studio_images(document):
        for section in document.sections:
            if section.image:
                ensure_image_exists(app,section.image)
            for item in section.items:
                if item.image:
                    ensure_image_exists(app,item.image)

    @app.put('/api/admin/estudio',tags=['Editor'])
    def save_studio(body: StudioInput, session: Annotated[dict, Depends(require_write)]):
        validate_studio_images(body.document)
        with database(app,write=True) as (conn, cursor):
            audit(cursor,'save_studio',str(locals().get('product_id','')),app.state.username)
            for section in body.document.sections:
                for reference in [section.image]+[x.image for x in section.items]:
                    if reference:validate_reference(cursor,reference)
            cursor.execute('UPDATE studio SET draft=%s,version=version+1 WHERE id=1 AND version=%s',(body.document.model_dump_json(),body.version))
            if cursor.rowcount == 0:
                raise HTTPException(409,'Otra pestaña guardó cambios. Recarga el editor antes de continuar.')
        app.state.content_cache.clear();app.state.catalog_cache.clear()
        return read_studio(True)

    @app.post('/api/admin/estudio/publicar',tags=['Editor'])
    def publish_studio(body: PublishInput, session: Annotated[dict, Depends(require_write)]):
        with database(app,write=True) as (conn, cursor):
            audit(cursor,'publish_studio',str(locals().get('product_id','')),app.state.username)
            cursor.execute('SELECT * FROM studio WHERE id=1 FOR UPDATE')
            row=cursor.fetchone()
            if body.version!=row['version']:
                raise HTTPException(409,'El borrador cambió. Recarga el editor.')
            cursor.execute('INSERT INTO studio_history(document,published_at) VALUES(%s,%s)',(row['published'], row['published_at'] or now_iso()))
            cursor.execute('DELETE FROM studio_history WHERE id NOT IN (SELECT id FROM studio_history ORDER BY id DESC LIMIT 10)')
            cursor.execute('UPDATE studio SET published=draft,version=version+1,published_at=%s WHERE id=1',(now_iso(),))
        app.state.content_cache.clear();app.state.catalog_cache.clear()
        return read_studio(True)

    @app.get('/api/admin/estudio/historial',tags=['Editor'])
    def history(session: Annotated[dict, Depends(require_admin)]):
        with database(app) as (conn, cursor):
            cursor.execute('SELECT id,published_at FROM studio_history ORDER BY id DESC')
            return [dict(r) for r in cursor.fetchall()]

    @app.post('/api/admin/estudio/restaurar/{revision}',tags=['Editor'])
    def restore_draft(revision:int, body:PublishInput, session: Annotated[dict, Depends(require_write)]):
        with database(app,write=True) as (conn, cursor):
            audit(cursor,'restore_draft',str(locals().get('product_id','')),app.state.username)
            cursor.execute('SELECT document FROM studio_history WHERE id=%s',(revision,))
            row=cursor.fetchone()
            if not row:
                raise HTTPException(404,'Versión no encontrada.')
            cursor.execute('UPDATE studio SET draft=%s,version=version+1 WHERE id=1 AND version=%s',(row['document'],body.version))
            if cursor.rowcount == 0:
                raise HTTPException(409,'El borrador cambió. Recarga el editor.')
        app.state.content_cache.clear();app.state.catalog_cache.clear()
        return read_studio(True)

    @app.get('/api/admin/respaldo',tags=['Sistema'])
    def backup(session: Annotated[dict,Depends(require_admin)],images:bool=False):
        temporary=tempfile.TemporaryDirectory(prefix='valerion-backup-');path=Path(temporary.name)/'Valerion_Respaldo_v3.zip'
        try:
            create_backup(app,path,include_images=images)
            return FileResponse(path,filename=path.name,media_type='application/zip',background=BackgroundTask(temporary.cleanup))
        except Exception:
            temporary.cleanup()
            raise HTTPException(502,'No se pudo completar el respaldo. Intenta de nuevo o elige la versión de datos y enlaces.')

    @app.get('/api/admin/mezcales',tags=['Catálogo'])
    def admin_products(session:Annotated[dict,Depends(require_admin)],q:str=Query('',max_length=100),page:int=Query(1,ge=1,le=10000),page_size:int=Query(20,ge=1,le=50)):
        return {**query_products(q,'recientes',True,page,page_size),'metrics':admin_metrics(app)}

    @app.get('/api/admin/mezcales/{product_id}',response_model=Product,tags=['Catálogo'])
    def admin_product(product_id:int,session:Annotated[dict,Depends(require_admin)]):
        with database(app) as (_,cursor):
            cursor.execute('SELECT * FROM mezcales WHERE id=%s',(product_id,));row=cursor.fetchone()
        if not row:raise HTTPException(404,'Producto no encontrado.')
        return public_product(row)

    @app.get('/api/admin/imagenes',tags=['Imágenes'])
    def assets(session:Annotated[dict,Depends(require_admin)]):
        with database(app) as (_,cursor):return {'items':usage(cursor)}

    @app.delete('/api/admin/imagenes/{asset_id}',status_code=204,tags=['Imágenes'])
    def remove_asset(asset_id:int,session:Annotated[dict,Depends(require_write)]):
        delete_unused(app,asset_id);return Response(status_code=204)

    @app.get('/admin/sistema',response_class=HTMLResponse,include_in_schema=False)
    def system_page(request:Request):
        if not current_session(request):return RedirectResponse('/admin/login',status_code=303)
        return templates.TemplateResponse(request=request,name='system.html',context={'username':app.state.username})

    @app.get('/api/admin/sistema',tags=['Sistema'])
    def diagnostics(session:Annotated[dict,Depends(require_admin)]):
        with database(app) as (_,cursor):
            cursor.execute('SELECT id,action,entity,actor,created_at FROM audit_log ORDER BY id DESC LIMIT 50');events=cursor.fetchall()
        return {'version':'3.1.0','pool':app.state.pool.get_stats(),'content_cache':app.state.content_cache.stats(),'catalog_cache':app.state.catalog_cache.stats(),'events':events}

    @app.get('/health',tags=['Sistema'])
    def health():return {'status':'ok'}

    @app.get('/ready',tags=['Sistema'])
    def ready():
        with database(app) as (_,cursor):cursor.execute('SELECT 1')
        return {'status':'ok'}

    @app.get("/admin/login", response_class=HTMLResponse, include_in_schema=False)
    def login_page(request: Request):
        if current_session(request):
            return RedirectResponse("/admin", status_code=303)
        return templates.TemplateResponse(request=request, name="login.html", context={})

    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    def admin_page(request: Request):
        if not current_session(request):
            return RedirectResponse("/admin/login", status_code=303)
        return templates.TemplateResponse(request=request, name="admin.html", context={"username": app.state.username})

    @app.get("/admin/contenido", response_class=HTMLResponse, include_in_schema=False)
    def content_page(request: Request):
        if not current_session(request):
            return RedirectResponse("/admin/login", status_code=303)
        return templates.TemplateResponse(request=request, name="content.html", context={"username": app.state.username, "fields": CONTENT_FIELDS})

    @app.get("/api/contenido", tags=["Contenido"])
    def get_content():
        return {"valores": read_content(app)}

    @app.put("/api/contenido", tags=["Contenido"])
    def save_content(body: ContentInput, session: Annotated[dict, Depends(require_write)]):
        with database(app,write=True) as (conn, cursor):
            audit(cursor,'save_content',str(locals().get('product_id','')),app.state.username)
            for key,value in body.valores.items():
                if FIELD_MAP[key]['type']=='image':validate_reference(cursor,value)
            for key, value in body.valores.items():
                cursor.execute("INSERT INTO site_content(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", (key, value))
        app.state.content_cache.clear();app.state.catalog_cache.clear()
        return {"valores": read_content(app)}

    @app.post('/api/auth/login',tags=['Autenticación'])
    def login(body:Login,request:Request,response:Response):
        client=request.client.host if request.client else 'local';timestamp=int(time.time());failure=None
        with database(app) as (_,cursor):
            cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('login:'+client,))
            cursor.execute('SELECT * FROM login_attempts WHERE client=%s',(client,));failed=cursor.fetchone()
            if failed and failed['failures']>=5 and timestamp-failed['last_attempt']<300:
                failure=429
            else:
                candidate=hashlib.scrypt(body.password.encode(),salt=app.state.salt,n=16384,r=8,p=1)
                ok=hmac.compare_digest(candidate,app.state.password_hash) and hmac.compare_digest(body.usuario.encode(),app.state.username.encode())
                if not ok:
                    cursor.execute('INSERT INTO login_attempts(client,failures,last_attempt) VALUES(%s,1,%s) ON CONFLICT(client) DO UPDATE SET failures=CASE WHEN login_attempts.last_attempt<%s THEN 1 ELSE login_attempts.failures+1 END,last_attempt=EXCLUDED.last_attempt',(client,timestamp,timestamp-300))
                    failure=401
                else:
                    token,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
                    cursor.execute('DELETE FROM login_attempts WHERE client=%s OR last_attempt<%s',(client,timestamp-86400))
                    cursor.execute('DELETE FROM sessions WHERE expires<=%s',(timestamp,))
                    old=request.cookies.get(COOKIE)
                    if old:cursor.execute('DELETE FROM sessions WHERE token_hash=%s',(hashlib.sha256(old.encode()).hexdigest(),))
                    cursor.execute('INSERT INTO sessions VALUES(%s,%s,%s,%s)',(hashlib.sha256(token.encode()).hexdigest(),csrf,timestamp+SESSION_SECONDS,app.state.fingerprint))
                    audit(cursor,'auth.login',actor=app.state.username)
        if failure:
            raise HTTPException(failure,'Demasiados intentos. Espera 5 minutos.' if failure==429 else 'Usuario o contraseña incorrectos.',headers={'Retry-After':'300'} if failure==429 else None)
        response.set_cookie(COOKIE,token,httponly=True,secure=app.state.secure_cookie,samesite='strict',max_age=SESSION_SECONDS,path='/')
        return {'usuario':app.state.username,'csrf_token':csrf}

    @app.get("/api/auth/me", tags=["Autenticación"])
    def me(session: Annotated[dict, Depends(require_admin)]):
        return {"usuario": app.state.username, "csrf_token": session["csrf"]}

    @app.post("/api/auth/logout", status_code=204, tags=["Autenticación"])
    def logout(response: Response, session: Annotated[dict, Depends(require_write)]):
        with database(app) as (conn, cursor):
            cursor.execute("DELETE FROM sessions WHERE token_hash=%s", (session["token_hash"],))
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict", secure=app.state.secure_cookie)

    @app.get('/api/mezcales',tags=['Catálogo'])
    def list_products(q:str=Query('',max_length=100),sort:Literal['recientes','precio_asc','precio_desc','nombre','destacados']='destacados',page:int=Query(1,ge=1,le=10000),page_size:int=Query(12,ge=1,le=48),category:str=Query('',max_length=60)):
        return {**query_products(q,sort,False,page,page_size,category),'categories':categories(app)}

    @app.get("/api/mezcales/{product_id}", response_model=Product, tags=["Catálogo"])
    def read_product(product_id: int):
        with database(app) as (conn, cursor):
            cursor.execute("SELECT * FROM mezcales WHERE id=%s", (product_id,))
            row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "No encontramos ese mezcal.")
        product=public_product(row)
        if not product["published"]:
            raise HTTPException(404,"Producto no encontrado.")
        return product

    @app.post("/api/mezcales", response_model=Product, status_code=201, tags=["Catálogo"])
    def create_product(body: ProductInput, response: Response, session: Annotated[dict, Depends(require_write)]):
        with database(app,write=True) as (conn, cursor):
            audit(cursor,'create_product',str(locals().get('product_id','')),app.state.username)
            for reference in [body.imagen_url]+[x.url for x in body.gallery]+[x.image for x in body.blocks if x.image]:
                validate_reference(cursor,reference)
            product_id = insert_product(cursor, body)
            cursor.execute("SELECT * FROM mezcales WHERE id=%s", (product_id,))
            row = cursor.fetchone()
        response.headers["Location"] = f"/api/mezcales/{product_id}"
        app.state.content_cache.clear();app.state.catalog_cache.clear()
        return public_product(row)

    @app.put("/api/mezcales/{product_id}", response_model=Product, tags=["Catálogo"])
    def update_product(product_id: int, body: ProductInput, session: Annotated[dict, Depends(require_write)]):
        with database(app,write=True) as (conn, cursor):
            audit(cursor,'update_product',str(locals().get('product_id','')),app.state.username)
            for reference in [body.imagen_url]+[x.url for x in body.gallery]+[x.image for x in body.blocks if x.image]:
                validate_reference(cursor,reference)
            cursor.execute("UPDATE mezcales SET nombre=%s,precio_centavos=%s,descripcion=%s,link_venta=%s,imagen_url=%s,actualizado_en=%s,extra_json=%s WHERE id=%s", (*values(body), now_iso(), extra_json(body), product_id))
            if cursor.rowcount == 0:
                raise HTTPException(404, "No encontramos ese mezcal.")
            cursor.execute("SELECT * FROM mezcales WHERE id=%s", (product_id,))
            row = cursor.fetchone()
        app.state.content_cache.clear();app.state.catalog_cache.clear()
        return public_product(row)

    @app.delete("/api/mezcales/{product_id}", status_code=204, tags=["Catálogo"])
    def delete_product(product_id: int, session: Annotated[dict, Depends(require_write)]):
        with database(app,write=True) as (conn, cursor):
            audit(cursor,'delete_product',str(locals().get('product_id','')),app.state.username)
            cursor.execute("DELETE FROM mezcales WHERE id=%s", (product_id,))
            if cursor.rowcount == 0:
                raise HTTPException(404, "No encontramos ese mezcal.")
        app.state.content_cache.clear();app.state.catalog_cache.clear()
        return Response(status_code=204)

    @app.get('/media/{filename}',include_in_schema=False)
    def uploaded_image(filename:str):
        if not MEDIA_NAME.fullmatch(filename):raise HTTPException(404,'Imagen no encontrada.')
        path=app.state.upload_dir/filename
        if not path.is_file():raise HTTPException(404,'Imagen no encontrada. Migra esta imagen antigua a Cloudinary.')
        return FileResponse(path,media_type='image/webp')

    @app.post('/api/imagenes',status_code=201,tags=['Imágenes'])
    def upload_image(session:Annotated[dict,Depends(require_write)],archivo:Annotated[UploadFile,File()]):
        try:return store_upload(app,archivo.file.read(MAX_UPLOAD+1))
        finally:archivo.file.close()

    attach_accounts(app,templates,page_context,require_admin,require_write)

    @app.post('/api/admin/sesiones/revocar',status_code=204)
    def revoke_sessions(session:Annotated[dict,Depends(require_write)]):
        with database(app) as (_,cur):
            cur.execute('DELETE FROM sessions')
            audit(cur,'admin.sessions.revoke',actor=app.state.username)
        return Response(status_code=204)

    return app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
