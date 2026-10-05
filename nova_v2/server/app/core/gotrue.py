"""
core/gotrue.py - the server's calls to Supabase Auth (GoTrue)  (Riley, 5.7)

The phone never talks to Supabase: sign-up, sign-in, refresh and sign-out go
phone -> FastAPI (api/auth.py) -> here -> GoTrue. So the anon key stays on the
server, and moving to a self-hosted Supabase (D99) needs no app rebuild.

Plain HTTP on purpose. Never sign in through supabase-py
(`get_client().auth.sign_in_*`): that client stores the session and swaps its
database Authorization header to the user's token, which would break the
shared service-role client (core/db.py) for every other request.

GoTrue's own errors come back as GoTrueError with its status and code, for the
route to pass on; a GoTrue that can't be reached is GoTrueUnavailable.
"""
from __future__ import annotations

from typing import Any, Literal

import httpx

from app.core.config import ConfigError, settings

_client = httpx.Client(timeout=10)


class GoTrueError(Exception):
    """GoTrue refused the request (bad password, weak password, rate limit...)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class GoTrueUnavailable(Exception):
    """GoTrue couldn't be reached, or isn't configured."""


def _call(method: str, path: str, *, json: Any = None, params: dict | None = None,
          token: str | None = None, admin: bool = False) -> dict[str, Any]:
    """`admin` sends the service-role key instead of the anon key, for
    /admin/... - only ever for the signed-in user's own account."""
    s = settings()
    try:
        s.require_auth()
        if admin:
            s.require_supabase()
    except ConfigError as e:
        raise GoTrueUnavailable(str(e)) from e
    headers = {"apikey": s.supabase_service_key if admin else s.supabase_anon_key}
    if admin:
        token = s.supabase_service_key
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = _client.request(method, f"{s.auth_url}{path}", json=json, params=params, headers=headers)
    except httpx.HTTPError as e:
        raise GoTrueUnavailable(str(e)) from e
    if r.status_code >= 500:
        raise GoTrueUnavailable(f"GoTrue {r.status_code}")
    try:
        body: dict[str, Any] = r.json() if r.content else {}
    except ValueError:
        body = {}
    if r.status_code >= 400:
        raise GoTrueError(
            r.status_code,
            str(body.get("error_code") or body.get("code") or body.get("error") or "auth_error"),
            str(body.get("msg") or body.get("message") or body.get("error_description") or "sign-in failed"),
        )
    return body


def signup(email: str, password: str) -> dict[str, Any]:
    """A session if email confirmation is off; otherwise just the new user."""
    return _call("POST", "/signup", json={"email": email, "password": password})


def login(email: str, password: str) -> dict[str, Any]:
    return _call("POST", "/token", params={"grant_type": "password"},
                 json={"email": email, "password": password})


def refresh(refresh_token: str) -> dict[str, Any]:
    """A new session. The old refresh token stops working (rotation)."""
    return _call("POST", "/token", params={"grant_type": "refresh_token"},
                 json={"refresh_token": refresh_token})


def logout(access_token: str, scope: Literal["local", "global"]) -> None:
    """Revoke refresh tokens: this device's ("local") or every device's ("global").
    Access tokens already issued stay valid until they expire."""
    _call("POST", "/logout", params={"scope": scope}, token=access_token)


def user_exists(user_id: str) -> bool:
    try:
        _call("GET", f"/admin/users/{user_id}", admin=True)
        return True
    except GoTrueError as e:
        if e.status == 404:
            return False
        raise


def delete_user(user_id: str) -> None:
    """Delete the account for good. Every table's user_id references
    auth.users with on delete cascade (db/schema.sql), so its rows go with it.
    An account that's already gone (404) counts as deleted."""
    try:
        _call("DELETE", f"/admin/users/{user_id}", admin=True)
    except GoTrueError as e:
        if e.status != 404:
            raise
