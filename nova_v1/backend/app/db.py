"""
db.py - shared Supabase client for the backend stores (Memory, Persona).

One cached service-role client; call get_client() to reach any table:

    from app.db import get_client
    get_client().table("episodic_memory").select("*").execute()

Requires SUPABASE_URL and SUPABASE_SERVICE_KEY (see config.py / .env.example).
"""
from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

# The backend is launched two ways: as the `app` package (scripts/tests, run from
# backend/) and bare from backend/app/ (uvicorn main:app). Support both.
try:
    from .config import settings          # app.db
except ImportError:                       # pragma: no cover
    from config import settings           # db (cwd = backend/app)


@lru_cache(maxsize=1)
def get_client() -> Client:
    """Return a cached Supabase client, or raise ConfigError if unconfigured."""
    s = settings()
    s.require_supabase()
    return create_client(s.supabase_url, s.supabase_service_key)
