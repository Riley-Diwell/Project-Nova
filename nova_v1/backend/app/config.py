"""Environment / configuration for the NOVA backend."""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()  # read backend/.env if present


class ConfigError(RuntimeError):
    """Raised when a required setting is missing."""


@lru_cache(maxsize=1)
def settings() -> "Settings":
    return Settings()


class Settings:
    def __init__(self) -> None:
        self.supabase_url = os.environ.get("SUPABASE_URL", "").strip()
        self.supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)

    def require_supabase(self) -> None:
        if not self.supabase_configured:
            raise ConfigError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set. "
                "Copy backend/.env.example to backend/.env and fill them in."
            )
