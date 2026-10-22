"""Bounded PostgreSQL connections, transactions and in-process public caches."""
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
import logging
import time
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log=logging.getLogger('valerion')
# All media-reference writes share this lock with explicit media cleanup.
REFERENCE_LOCK=708143010

class TTLCache:
    def __init__(self,max_entries=128):
        self.entries=OrderedDict(); self.lock=RLock(); self.max_entries=max_entries
        self.generation=0; self.hits=0; self.misses=0
    def get(self,key,loader,ttl=60):
        with self.lock:
            record=self.entries.get(key)
            if record and record[0]>time.monotonic():
                self.entries.move_to_end(key);self.hits+=1
                return deepcopy(record[1])
            self.entries.pop(key,None);self.misses+=1;generation=self.generation
        value=loader()
        with self.lock:
            if generation==self.generation:
                self.entries[key]=(time.monotonic()+ttl,deepcopy(value))
                while len(self.entries)>self.max_entries:self.entries.popitem(last=False)
        return value
    def clear(self):
        with self.lock:self.entries.clear();self.generation+=1
    def stats(self):
        with self.lock:return {'entries':len(self.entries),'hits':self.hits,'misses':self.misses}

def make_pool(url,config,configure=None):
    # Do not log pool connection errors containing hostnames or connection strings.
    logging.getLogger('psycopg.pool').disabled=True
    return ConnectionPool(conninfo=url,open=False,min_size=0,configure=configure,
        max_size=int(config.get('DB_POOL_MAX',5)),max_idle=60,max_lifetime=900,
        timeout=12,max_waiting=20,reconnect_timeout=10,
        check=ConnectionPool.check_connection,
        kwargs={'row_factory':dict_row,'connect_timeout':8,'prepare_threshold':None},name='valerion')

@contextmanager
def database(app,write=False):
    with app.state.pool.connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout='15000'; SET LOCAL lock_timeout='5000'")
            if write:cursor.execute('SELECT pg_advisory_xact_lock(%s)',(REFERENCE_LOCK,))
            yield conn,cursor

def audit(cursor,action,entity='',actor='admin'):
    cursor.execute('INSERT INTO audit_log(action,entity,actor,created_at) VALUES(%s,%s,%s,%s)',
                   (action,entity,actor,int(time.time())))

PUBLISHED="COALESCE((extra_json::jsonb->>'published')::boolean,true)"
FEATURED="COALESCE((extra_json::jsonb->>'featured')::boolean,false)"
CATEGORY="COALESCE(extra_json::jsonb->>'category','')"
SEARCH="(nombre || ' ' || descripcion || ' ' || COALESCE(extra_json::jsonb->>'category',''))"
ORDER={'recientes':'id DESC','precio_asc':'precio_centavos ASC,id DESC','precio_desc':'precio_centavos DESC,id DESC','nombre':'LOWER(nombre) ASC,id DESC','destacados':FEATURED+' DESC,id DESC'}

def catalog_page(app,convert,query='',sort='recientes',include_drafts=False,page=1,page_size=12,category='',exclude=None):
    def load():
        conditions=[];params=[]
        if not include_drafts:conditions.append(PUBLISHED+'=true')
        if query.strip():
            value=query.strip().replace('\\','\\\\').replace('%',r'\%').replace('_',r'\_')
            conditions.append(SEARCH+" ILIKE %s ESCAPE E'\\\\'");params.append('%'+value+'%')
        if category:conditions.append(CATEGORY+'=%s');params.append(category)
        if exclude is not None:conditions.append('id<>%s');params.append(exclude)
        where=' WHERE '+' AND '.join(conditions) if conditions else ''
        with database(app) as (_,cursor):
            cursor.execute('SELECT count(*) AS total FROM mezcales'+where,params);total=cursor.fetchone()['total']
            # Page responses omit long descriptions/collections: edit and detail fetch one record.
            columns="id,nombre,precio_centavos,descripcion,link_venta,imagen_url,creado_en,actualizado_en,(extra_json::jsonb - 'gallery' - 'blocks' - 'specs')::text AS extra_json"
            cursor.execute('SELECT '+columns+' FROM mezcales'+where+' ORDER BY '+ORDER[sort]+' LIMIT %s OFFSET %s',(*params,page_size,(page-1)*page_size))
            items=[convert(row) for row in cursor.fetchall()]
        return {'items':items,'total':total,'page':page,'page_size':page_size,'pages':max(1,(total+page_size-1)//page_size)}
    if include_drafts:return load()
    return app.state.catalog_cache.get((query,sort,page,page_size,category,exclude),load,ttl=15)

def categories(app,include_drafts=False):
    def load():
        with database(app) as (_,cursor):
            cursor.execute('SELECT DISTINCT '+CATEGORY+' AS category FROM mezcales WHERE '+CATEGORY+"<>''"+('' if include_drafts else ' AND '+PUBLISHED+'=true')+' ORDER BY category LIMIT 200')
            return [r['category'] for r in cursor.fetchall()]
    return app.state.catalog_cache.get(('categories',include_drafts),load,ttl=15)

def admin_metrics(app):
    with database(app) as (_,cursor):
        cursor.execute('SELECT count(*) AS total,COALESCE(avg(precio_centavos),0) AS average_cents,count(*) FILTER (WHERE link_venta IS NOT NULL) AS links,count(*) FILTER (WHERE '+PUBLISHED+') AS published FROM mezcales')
        r=cursor.fetchone();r['average_cents']=float(r['average_cents']);return r
