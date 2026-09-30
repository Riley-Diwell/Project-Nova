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
          token: str | None = None) -> dict[str, Any]:
    s = settings()
    try:
        s.require_auth()
    except ConfigError as e:
        raise GoTrueUnavailable(str(e)) from e
    headers = {"apikey": s.supabase_anon_key}
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
