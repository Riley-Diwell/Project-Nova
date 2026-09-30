"""Environment / configuration for the NOVA backend."""
from __future__ import annotations

import os
from functools import lru_cache
from uuid import UUID

from dotenv import load_dotenv

load_dotenv()  # read server/.env if present


class ConfigError(RuntimeError):
    """Raised when a required setting is missing."""


@lru_cache(maxsize=1)
def settings() -> "Settings":
    return Settings()


class Settings:
    def __init__(self) -> None:
        self.supabase_url = os.environ.get("SUPABASE_URL", "").strip()
        self.supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

        # Accounts. The anon/publishable
        # key is only sent to GoTrue by app/core/gotrue.py; it never reaches the
        # phone. SUPABASE_JWT_SECRET is for self-hosted Supabase, which signs
        # with HS256 - leave it unset for Supabase Cloud, whose tokens are
        # checked against the project's public keys (JWKS) instead.
        self.supabase_anon_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
        self.supabase_jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "").strip()

        # Local development only: every request is this user, no token needed.
        # app/core/auth.py refuses to start with it on Cloud Run.
        self.auth_disabled = os.environ.get("NOVA_AUTH_DISABLED", "").strip() == "1"
        self.dev_user_id = UUID(
            os.environ.get("NOVA_DEV_USER_ID", "").strip() or "00000000-0000-0000-0000-000000000001"
        )

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)

    def require_supabase(self) -> None:
        if not self.supabase_configured:
            raise ConfigError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set. "
                "Copy server/.env.example to server/.env and fill them in."
            )

    @property
    def auth_url(self) -> str:
        """GoTrue's base URL, e.g. https://<ref>.supabase.co/auth/v1."""
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    def require_auth(self) -> None:
        if not (self.supabase_url and self.supabase_anon_key):
            raise ConfigError(
                "SUPABASE_URL and SUPABASE_ANON_KEY must be set for sign-up and sign-in."
            )
