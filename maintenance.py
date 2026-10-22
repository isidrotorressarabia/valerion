"""Offline maintenance context; never logs connection strings."""
from contextlib import contextmanager
from types import SimpleNamespace
import os
from dotenv import dotenv_values
from configurar import ROOT
from infrastructure import make_pool,TTLCache
from media_store import cloud_settings

@contextmanager
def maintenance():
    config={**dotenv_values(ROOT/'.env'),**os.environ}
    if not config.get('DATABASE_URL'):raise ValueError('Configura DATABASE_URL.')
    state=SimpleNamespace(pool=make_pool(config['DATABASE_URL'],config),cloud=cloud_settings(config.get('CLOUDINARY_URL')),media_folder=config.get('CLOUDINARY_FOLDER','valerion_catalogo'),content_cache=TTLCache(),catalog_cache=TTLCache())
    app=SimpleNamespace(state=state);state.pool.open()
    try:yield app
    finally:state.pool.close()
