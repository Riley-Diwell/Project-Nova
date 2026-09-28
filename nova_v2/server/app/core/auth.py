"""
core/auth.py - who is calling  (Riley, 5.7)

`current_user` is the FastAPI dependency every user-facing route takes:

    @router.get("/me")
    def me(user: AuthUser = Depends(current_user)): ...

It checks the Supabase access token (a JWT) in `Authorization: Bearer ...`
locally - no round trip to Supabase per request - and returns the user it
names. The user id only ever comes from a verified token, never from a request
body.

Two signing setups, picked by config:
  - Supabase Cloud signs with an asymmetric key (ES256 today). The public keys
    come from the project's JWKS endpoint and are cached by PyJWKClient.
  - Self-hosted Supabase signs with a shared secret (HS256), given as
    SUPABASE_JWT_SECRET.
The accepted algorithms are fixed per setup and never read from the token's
own header, so `alg=none` and HS256-signed-with-the-public-key tokens fail.

Any token problem is a 401 with `WWW-Authenticate: Bearer`; the phone reacts
by refreshing once, then signing out. A 403 means the client key was wrong
(main.py), which a refresh can't fix.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

# Supabase gives every signed-in user's token this audience.
_AUDIENCE = "authenticated"
# Tolerated clock skew between Supabase and Cloud Run, in seconds.
_LEEWAY = 30
_ASYMMETRIC_ALGS = ["ES256", "RS256"]

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthUser:
    id: UUID
    email: str | None


def check_startup() -> None:
    """Refuse to run with auth switched off on Cloud Run (which sets K_SERVICE)."""
    if settings().auth_disabled and os.environ.get("K_SERVICE"):
        raise RuntimeError("NOVA_AUTH_DISABLED is set - refusing to start on Cloud Run with it.")


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    # Caches the fetched keys; an unknown `kid` (after a key rotation)
    # triggers one refetch.
    return jwt.PyJWKClient(f"{settings().auth_url}/.well-known/jwks.json", timeout=5)


def _key_for(token: str) -> tuple[object, list[str]]:
    """The key to check this token with, and the algorithms allowed with it."""
    secret = settings().supabase_jwt_secret
    if secret:
        return secret, ["HS256"]
    return _jwks_client().get_signing_key_from_jwt(token).key, _ASYMMETRIC_ALGS


def verify_token(token: str) -> AuthUser:
    """The user a valid access token names. Raises jwt.PyJWTError otherwise."""
    key, algorithms = _key_for(token)
    claims = jwt.decode(
        token,
        key,
        algorithms=algorithms,
        audience=_AUDIENCE,
        options={"require": ["exp", "sub"]},
        leeway=_LEEWAY,
    )
    try:
        user_id = UUID(claims["sub"])
    except (TypeError, ValueError) as e:
        raise jwt.InvalidTokenError("sub is not a user id") from e
    return AuthUser(id=user_id, email=claims.get("email"))


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def bearer_token(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str | None:
    """The raw access token, for the few routes that pass it on to GoTrue."""
    return creds.credentials if creds else None


# The only routes callable without signing in: uptime checks, and the routes that
# hand out a token in the first place. Everything else needs one (require_user).
PUBLIC_ROUTES = frozenset({"/health", "/auth/signup", "/auth/login", "/auth/refresh"})


def require_user(request: Request, token: str | None = Depends(bearer_token)) -> None:
    """App-wide dependency (main.py): every route needs a signed-in user unless it's in
    PUBLIC_ROUTES. Fail-closed, so a new route is protected without having to remember to be.
    Routes that need to know *who* still take `Depends(current_user)` themselves."""
    route = request.scope.get("route")
    if getattr(route, "path", None) in PUBLIC_ROUTES:
        return
    current_user(token)


def current_user(token: str | None = Depends(bearer_token)) -> AuthUser:
    s = settings()
    if s.auth_disabled:
        return AuthUser(id=s.dev_user_id, email=None)
    if not token:
        raise _unauthorized("missing bearer token")
    try:
        return verify_token(token)
    except jwt.PyJWKClientConnectionError:
        # Supabase's key endpoint is unreachable: not the token's fault, so
        # not a 401 (which would make the phone sign the user out).
        raise HTTPException(status_code=503, detail="can't verify sign-in right now")
    except jwt.PyJWTError:
        raise _unauthorized("invalid or expired token")
