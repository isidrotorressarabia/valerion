"""Valerion: catálogo con FastAPI, Jinja2, PostgreSQL (Neon), Cloudinary y sesiones revocables."""
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import tempfile
from starlette.background import BackgroundTask
import os
import re
from pathlib import Path
import secrets
import time
from typing import Annotated, Literal

import logging
import uuid
import psycopg
from psycopg_pool import PoolTimeout,TooManyRequests
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.gzip import GZipMiddleware
from infrastructure import database,make_pool,TTLCache,catalog_page,categories,admin_metrics,audit,public_revision
from migrations import upgrade
from accounts import throttle, migrate as migrate_accounts, attach as attach_accounts, current as current_customer, customer
from services import commerce, seo, admin_auth
from services.admin_auth import current_session, require_admin, require_write
from services.catalog import ContentInput, ProductInput, Product, now_iso, public_product, insert_product, values, extra_json, MEDIA_NAME
from services.crm import attach_crm
from services.logistics import upgrade as upgrade_logistics
from services.payments import MercadoPago
from services.privacy import DeliveryVault
from services.settings import Settings, database_dsn
from services.http import BrowserPolicy
from starlette.middleware.cors import CORSMiddleware
from services.api_docs import attach_docs
from media_store import cloud_settings,store_upload,optimized_url,usage,delete_unused
from backups import create_backup
log=logging.getLogger('valerion')
from dotenv import dotenv_values
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, File, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import cloudinary
import cloudinary.uploader


from configurar import ROOT, ensure_config
from content import CONTENT_FIELDS, DEFAULT_CONTENT, FIELD_MAP, LEGACY_REPLACEMENTS
from cms import Studio, StudioInput, PublishInput, theme_css, whatsapp_link

MAX_UPLOAD = 8 * 1024 * 1024
AGAVE_IMAGE = "https://images.unsplash.com/photo-1732043846982-5522e1e2626c?auto=format&fit=crop&w=1000&q=85"
DEMO_PRODUCTS = [
    {"nombre": "Valerion Espadín", "precio": "890.00", "descripcion": "Propuesta de colección: una expresión de entrada para descubrir el universo Valerion.", "link_venta": None, "imagen_url": AGAVE_IMAGE},
    {"nombre": "Valerion Tobalá", "precio": "1490.00", "descripcion": "Propuesta de colección: una expresión para quienes disfrutan explorar nuevos matices.", "link_venta": None, "imagen_url": AGAVE_IMAGE},
    {"nombre": "Valerion Ensamble", "precio": "1890.00", "descripcion": "Propuesta de colección: un encuentro de agaves con identidad propia.", "link_venta": None, "imagen_url": AGAVE_IMAGE},
]

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
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS ordenes (
            id SERIAL PRIMARY KEY, producto_id INTEGER REFERENCES mezcales(id),
            monto_centavos INTEGER NOT NULL, estado TEXT NOT NULL DEFAULT 'pendiente',
            pago_id TEXT, creado_en TEXT NOT NULL
        );
        """)
        
        upgrade(cursor,conn)
        migrate_accounts(cursor)
        upgrade_logistics(cursor, app.state.vault)
        initial = Studio().model_dump_json()
        cursor.execute('INSERT INTO studio(id,draft,published,version) VALUES(1,%s,%s,1) ON CONFLICT (id) DO NOTHING', (initial,initial))
        
        cursor.execute("""INSERT INTO site_content(key,value) SELECT * FROM unnest(%s::text[],%s::text[])
                          ON CONFLICT (key) DO NOTHING""",(list(DEFAULT_CONTENT),list(DEFAULT_CONTENT.values())))
            
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
    config = {**dotenv_values(ROOT/'.env'), **os.environ}
    settings = Settings.load(config)
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings
        app.state.vault = DeliveryVault(config.get('DELIVERY_KEYS',''))
        production=config.get('APP_ENV')=='production'
        password=admin_password if admin_password is not None else config.get('ADMIN_PASSWORD','')
        if not password or not 12<=len(password)<=256:
            raise RuntimeError('Configura ADMIN_PASSWORD con entre 12 y 256 caracteres.')
        app.state.username=admin_username or config.get('ADMIN_USERNAME','admin')
        app.state.secure_cookie=production or str(config.get('COOKIE_SECURE','false')).lower()=='true'
        app.state.seed_demo=seed_demo if seed_demo is not None else str(config.get('SEED_DEMO','false')).lower()=='true'
        url=db_url or config.get('DATABASE_URL')
        if not url:raise RuntimeError('Configura DATABASE_URL con PostgreSQL/Neon.')
        url=database_dsn(url,production)
        if not 1<=int(config.get('DB_POOL_MAX',5))<=10:raise RuntimeError('DB_POOL_MAX debe estar entre 1 y 10.')
        try:app.state.cloud=cloud_settings(config.get('CLOUDINARY_URL'))
        except ValueError:raise RuntimeError('Configura CLOUDINARY_URL antes de iniciar.') from None
        
        mp_token = config.get("MERCADOPAGO_ACCESS_TOKEN")
        app.state.gateway = MercadoPago(mp_token) if mp_token else None
        
        app.state.cloud_uploader=cloudinary.uploader.upload
        app.state.cloud_destroy=cloudinary.uploader.destroy
        app.state.media_folder=config.get('CLOUDINARY_FOLDER','valerion_catalogo')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',app.state.media_folder):raise RuntimeError('CLOUDINARY_FOLDER inválido.')
        app.state.content_cache=TTLCache(16,lambda:public_revision(app));app.state.catalog_cache=TTLCache(128,lambda:public_revision(app))
        app.state.pool=make_pool(url,config)
        app.state.upload_dir = ROOT / "data" / "uploads"
        app.state.upload_dir.mkdir(parents=True, exist_ok=True)
        app.state.salt = secrets.token_bytes(16)
        app.state.password_hash = hashlib.scrypt(password.encode(), salt=app.state.salt, n=16384, r=8, p=1)
        app.state.fingerprint = hashlib.scrypt(password.encode(), salt=("valerion-admin:"+app.state.username).encode(), n=32768, r=8, p=1, maxmem=67108864).hex()
        app.state.pool.open()
        try:
            if config.get("AUTO_MIGRATE","true").lower()=="true":
                initialize(app)
            else:
                with database(app) as (_,cur):
                    cur.execute("SELECT value FROM metadata WHERE key='commerce_schema'")
                    schema=cur.fetchone()
                    if not schema or schema["value"]!="4":
                        raise RuntimeError("Ejecuta la migración antes de servir tráfico.")
                    cur.execute("SELECT value FROM metadata WHERE key='delivery_key_check'")
                    app.state.vault.open("metadata:delivery_key_check",cur.fetchone()["value"])
            yield
        except (psycopg.Error,PoolTimeout):
            raise RuntimeError('No se pudo preparar PostgreSQL. Revisa conectividad, TLS y permisos de migración.') from None
        finally:
            app.state.pool.close()
            if app.state.gateway:
                app.state.gateway.http.close()

    app = FastAPI(title="Valerion · API de catálogo y contenido", version="4.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(GZipMiddleware,minimum_size=1200)
    app.add_middleware(CORSMiddleware,allow_origins=[settings.origin],allow_credentials=True,
                       allow_methods=['GET','HEAD','POST','PUT','PATCH','DELETE'],allow_headers=['Content-Type','X-CSRF-Token'])
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=list(settings.hosts))
    app.add_middleware(BrowserPolicy,settings=settings)
    attach_docs(app)
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

    manifest=json.loads((ROOT/"static"/"build"/"manifest.json").read_text())
    templates.env.globals["asset"] = lambda name: "/static/build/"+manifest[name]
    templates.env.globals["collection_path"] = seo.collection_path
    templates.env.globals["product_path"] = seo.product_path
    templates.env.filters['image_size']=optimized_url
    templates.env.filters["mxn"] = lambda value: f"${Decimal(str(value)):,.2f}"

    @app.exception_handler(psycopg.Error)
    @app.exception_handler(PoolTimeout)
    @app.exception_handler(TooManyRequests)
    async def database_error(request, exc):
        log.error("database_error id=%s type=%s", request.state.request_id, type(exc).__name__)
        return JSONResponse({"detail":"Servicio temporalmente no disponible.","request_id":request.state.request_id},503)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        errors = [{"campo": str(e["loc"][-1]), "mensaje": e["msg"]} for e in exc.errors()]
        return JSONResponse({"detail": "Revisa los campos indicados.", "errores": errors}, status_code=422)

    def query_products(query='',sort='destacados',include_drafts=False,page=1,page_size=12,category='',exclude=None):
        return catalog_page(app,public_product,query,sort,include_drafts,page,page_size,category,exclude)

    def public_html(request:Request, name:str, context:dict) -> Response:
        response=templates.TemplateResponse(request=request,name=name,context=context)
        if request.headers.get("cookie"):
            return response
        tag='W/"'+hashlib.sha256(response.body).hexdigest()[:32]+'"'
        request.state.public_html=True
        if tag in [t.strip() for t in request.headers.get("if-none-match","").split(",")]:
            return Response(status_code=304,headers={"ETag":tag,"Vary":"Cookie, Accept-Encoding"})
        response.headers["ETag"]=tag
        response.headers["Vary"]="Cookie, Accept-Encoding"
        return response

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request):
        catalog = query_products()
        return public_html(request,"index.html",{"products":catalog["items"],
                "catalog":catalog,"categories":categories(app),"seo":seo.metadata(settings.origin,read_content(app)),**page_context()})

    @app.get("/coleccion",response_class=HTMLResponse,include_in_schema=False)
    @app.get("/coleccion/pagina/{page}",response_class=HTMLResponse,include_in_schema=False)
    def collection(request:Request,page:int=1):
        if not 1<=page<=10000:
            raise HTTPException(404)
        if request.url.path!=seo.collection_path(page):
            return RedirectResponse(seo.collection_path(page),301)
        catalog=query_products(page=page)
        if page>catalog["pages"]:
            raise HTTPException(404,"Página no encontrada.")
        content=read_content(app)
        meta=seo.metadata(settings.origin,content)
        meta["title"]=f"Colección de mezcales · {content['brand_name']}"+(f" · Página {page}" if page>1 else "")
        meta["canonical"]=settings.origin+seo.collection_path(page)
        meta["graph"]["@graph"].append({"@type":"CollectionPage","url":meta["canonical"],"name":meta["title"],
             "mainEntity":{"@type":"ItemList","itemListElement":[{"@type":"ListItem","position":i,
                 "url":settings.origin+item["ruta"]} for i,item in enumerate(catalog["items"],1)]}})
        return public_html(request,"collection.html",{"catalog":catalog,
            "products":catalog["items"],"categories":categories(app),"seo":meta,"is_collection":True,**page_context()})

    @app.get('/admin/preview', response_class=HTMLResponse, include_in_schema=False)
    def preview_page(request: Request, session: Annotated[dict, Depends(require_admin)]):
        return templates.TemplateResponse(request=request, name='index.html', context={'products':query_products()['items'],'catalog':query_products(),'categories':categories(app), **page_context(True)})

    @app.get('/theme.css', include_in_schema=False)
    def dynamic_theme(request: Request, draft: bool=False):
        if draft:require_admin(request)
        return Response(theme_css(read_studio(draft)['document']['theme']), media_type='text/css')

    def published_product(product_id: int):
        with database(app) as (conn, cursor):
            cursor.execute('SELECT * FROM mezcales WHERE id=%s', (product_id,))
            row = cursor.fetchone()
        if not row:
            raise HTTPException(404, 'Producto no encontrado.')
        product = public_product(row)
        if not product['published']:
            raise HTTPException(404, 'Producto no encontrado.')
        return product

    @app.get("/api/mezcales/{product_id}",response_model=Product,tags=["Catálogo"])
    def public_detail(product_id:int):
        return published_product(product_id)

    @app.get('/producto/{product_id}', response_class=HTMLResponse, include_in_schema=False)
    @app.get('/producto/{product_id}/{slug}', response_class=HTMLResponse, include_in_schema=False)
    def product_page(product_id:int, request:Request, slug:str|None=None):
        product = published_product(product_id)
        path = seo.product_path(product)
        if request.url.path != path:
            # Only the existing public payment-state flags survive normalization.
            state=request.query_params.get('pago')
            return RedirectResponse(path+('?pago='+state if state in {'fallido','pendiente'} else ''),301)
        ctx = page_context()
        ctx["seo"] = seo.metadata(settings.origin,read_content(app),product)
        ctx["checkout_key"] = str(uuid.uuid4())
        ctx['customer'] = current_customer(request)
        ctx['product_whatsapp'] = whatsapp_link(ctx['contact'], f"Hola, me interesa {product['nombre']}. ¿Podrían darme más información?")
        ctx['related'] = query_products(page_size=3,exclude=product_id)['items']
        return public_html(request,'product.html',{'product': product, **ctx})

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
            audit(cursor,'save_studio','',app.state.username)
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
            audit(cursor,'publish_studio','',app.state.username)
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
            audit(cursor,'restore_draft','',app.state.username)
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
        throttle(app,"admin-backup:"+app.state.username,6)
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
        return {'version':'4.0.0','pool':app.state.pool.get_stats(),'content_cache':app.state.content_cache.stats(),'catalog_cache':app.state.catalog_cache.stats(),'events':events}

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
            audit(cursor,'save_content','',app.state.username)
            for key,value in body.valores.items():
                if FIELD_MAP[key]['type']=='image':validate_reference(cursor,value)
            for key, value in body.valores.items():
                cursor.execute("INSERT INTO site_content(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", (key, value))
        app.state.content_cache.clear();app.state.catalog_cache.clear()
        return {"valores": read_content(app)}

    @app.get('/api/mezcales',tags=['Catálogo'])
    def list_products(q:str=Query('',max_length=100),sort:Literal['recientes','precio_asc','precio_desc','nombre','destacados']='destacados',page:int=Query(1,ge=1,le=10000),page_size:int=Query(12,ge=1,le=48),category:str=Query('',max_length=60)):
        return {**query_products(q,sort,False,page,page_size,category),'categories':categories(app)}

    @app.post("/api/mezcales", response_model=Product, status_code=201, tags=["Catálogo"])
    def create_product(body: ProductInput, response: Response, session: Annotated[dict, Depends(require_write)]):
        with database(app,write=True) as (conn, cursor):
            for reference in [body.imagen_url]+[x.url for x in body.gallery]+[x.image for x in body.blocks if x.image]:
                validate_reference(cursor,reference)
            product_id = insert_product(cursor, body)
            audit(cursor,'create_product',str(product_id),app.state.username)
            cursor.execute("SELECT * FROM mezcales WHERE id=%s", (product_id,))
            row = cursor.fetchone()
        response.headers["Location"] = f"/api/mezcales/{product_id}"
        app.state.content_cache.clear();app.state.catalog_cache.clear()
        return public_product(row)

    @app.put("/api/mezcales/{product_id}", response_model=Product, tags=["Catálogo"])
    def update_product(product_id: int, body: ProductInput, session: Annotated[dict, Depends(require_write)]):
        with database(app,write=True) as (conn, cursor):
            audit(cursor,'update_product',str(product_id),app.state.username)
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
            audit(cursor,'delete_product',str(product_id),app.state.username)
            cursor.execute("SELECT 1 FROM ordenes WHERE producto_id=%s LIMIT 1",(product_id,))
            if cursor.fetchone():
                raise HTTPException(409,"Hay órdenes para este mezcal. Puedes retirarlo del catálogo desactivando su publicación.")
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
        try:
            throttle(app,"admin-images:"+app.state.username,80)
            return store_upload(app,archivo.file.read(MAX_UPLOAD+1))
        finally:archivo.file.close()

    attach_accounts(app,templates,page_context,require_admin,require_write)

    admin_auth.attach(app)
    attach_crm(app,customer,require_admin,require_write)
    commerce.attach(app,templates,require_admin,require_write,current_session)
    seo.attach(app)
    return app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    ensure_config()
    uvicorn.run("main:app", host="127.0.0.1", port=8000)
