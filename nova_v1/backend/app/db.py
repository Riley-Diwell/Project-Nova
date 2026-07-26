"""Builds and caches the Supabase client - the only module that imports
`supabase` directly.
"""
from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from .config import settings


@lru_cache(maxsize=1)
def get_client() -> Client:
    cfg = settings()
    cfg.require_supabase()
    return create_client(cfg.supabase_url, cfg.supabase_service_key)
