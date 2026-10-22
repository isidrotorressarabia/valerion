"""Additive PostgreSQL migration; existing product ids and text values are preserved."""
from infrastructure import PUBLISHED,FEATURED,CATEGORY,SEARCH

def upgrade(cursor,conn):
    cursor.execute('''CREATE TABLE IF NOT EXISTS media_assets (
      id BIGSERIAL PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, public_id TEXT UNIQUE NOT NULL,
      asset_id TEXT, url TEXT UNIQUE NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,
      bytes BIGINT NOT NULL,created_at BIGINT NOT NULL,deleted_at BIGINT);
      CREATE TABLE IF NOT EXISTS audit_log(id BIGSERIAL PRIMARY KEY,action TEXT NOT NULL,
      entity TEXT NOT NULL DEFAULT '',actor TEXT NOT NULL,created_at BIGINT NOT NULL);
      CREATE INDEX IF NOT EXISTS sessions_expires_idx ON sessions(expires);
      CREATE INDEX IF NOT EXISTS login_attempts_time_idx ON login_attempts(last_attempt);
      CREATE INDEX IF NOT EXISTS audit_log_time_idx ON audit_log(created_at);
      CREATE INDEX IF NOT EXISTS media_assets_created_idx ON media_assets(created_at);
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS catalog_public_price_idx ON mezcales(precio_centavos,id) WHERE '+PUBLISHED+'=true')
    cursor.execute('CREATE INDEX IF NOT EXISTS catalog_category_idx ON mezcales(('+CATEGORY+'),id) WHERE '+PUBLISHED+'=true')
    cursor.execute('CREATE INDEX IF NOT EXISTS catalog_name_idx ON mezcales(LOWER(nombre),id) WHERE '+PUBLISHED+'=true')
    cursor.execute('CREATE INDEX IF NOT EXISTS catalog_featured_idx ON mezcales(('+FEATURED+') DESC,id DESC) WHERE '+PUBLISHED+'=true')
    try:
        with conn.transaction():
            cursor.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
            cursor.execute('CREATE INDEX IF NOT EXISTS catalog_search_idx ON mezcales USING gin ('+SEARCH+' gin_trgm_ops)')
    except Exception:
        # Search remains correct without optional extension/DDL permission.
        pass
    cursor.execute("INSERT INTO metadata(key,value) VALUES('schema_version','3') ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value")
