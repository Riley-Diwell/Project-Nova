"""
store/canvas.py - each user's Canvas connection

One row per account in `canvas_connections` (db/schema.sql section 9): the
Canvas address the user signs in at, and the personal access token they made
under Account -> Settings -> Approved Integrations.

    get(user_id)                         -> CanvasConnection or None
    save(user_id, base_url, token, ...)  -> CanvasConnection
    delete(user_id)
    is_connected(user_id)                -> bool, never raises

THE TOKEN
It can read everything the student can in Canvas - grades included - so it is
kept encrypted (Fernet) and never leaves the server: not in a response, not in
a log line, not in a tool result. The key is CANVAS_TOKEN_KEY when set, and
otherwise derived from SUPABASE_JWT_SECRET, which every deployment already has.
Changing either makes the stored tokens unreadable; get() then answers None, and
the user connects again.

The table has row-level security on and no policy at all, so only the service
role - this server - can read it. A user's own JWT reaches nothing here.

Tests swap the Supabase store for InMemoryCanvasStore with set_store().
"""
from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, Union
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from app.core.db import get_client

UserId = Union[UUID, str]

_TABLE = "canvas_connections"


class CanvasNotConfigured(RuntimeError):
    """No key to encrypt tokens with - the server has neither secret set."""


@dataclass(frozen=True)
class CanvasConnection:
    base_url: str
    token: str
    canvas_user_name: Optional[str]
    connected_at: Optional[str]


def _fernet() -> Fernet:
    key = os.environ.get("CANVAS_TOKEN_KEY", "").strip()
    if key:
        return Fernet(key.encode())
    secret = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
    if not secret:
        raise CanvasNotConfigured("set CANVAS_TOKEN_KEY or SUPABASE_JWT_SECRET")
    # Domain-separated, so this key is never the JWT secret itself.
    digest = hashlib.sha256(b"nova-canvas-token-v1:" + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def _decrypt(ciphertext: str) -> Optional[str]:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, CanvasNotConfigured, ValueError):
        return None


class CanvasStore(Protocol):
    def get(self, user_id: UserId) -> Optional[dict[str, Any]]: ...
    def put(self, user_id: UserId, row: dict[str, Any]) -> None: ...
    def delete(self, user_id: UserId) -> None: ...


class SupabaseCanvasStore:
    def get(self, user_id: UserId) -> Optional[dict[str, Any]]:
        rows = get_client().table(_TABLE).select("*").eq("user_id", str(user_id)).limit(1).execute().data
        return rows[0] if rows else None

    def put(self, user_id: UserId, row: dict[str, Any]) -> None:
        get_client().table(_TABLE).upsert({**row, "user_id": str(user_id)}, on_conflict="user_id").execute()

    def delete(self, user_id: UserId) -> None:
        get_client().table(_TABLE).delete().eq("user_id", str(user_id)).execute()


class InMemoryCanvasStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def get(self, user_id: UserId) -> Optional[dict[str, Any]]:
        row = self.rows.get(str(user_id))
        return dict(row) if row else None

    def put(self, user_id: UserId, row: dict[str, Any]) -> None:
        self.rows[str(user_id)] = {**row, "user_id": str(user_id)}

    def delete(self, user_id: UserId) -> None:
        self.rows.pop(str(user_id), None)


_store: Optional[CanvasStore] = None


def set_store(store: Optional[CanvasStore]) -> None:
    global _store
    _store = store


def _get_store() -> CanvasStore:
    global _store
    if _store is None:
        _store = SupabaseCanvasStore()
    return _store


def get(user_id: UserId) -> Optional[CanvasConnection]:
    row = _get_store().get(user_id)
    if not row:
        return None
    token = _decrypt(row.get("token_ciphertext") or "")
    if token is None:
        print("[canvas] stored token unreadable (key changed?) - treating as not connected")
        return None
    return CanvasConnection(
        base_url=row["base_url"],
        token=token,
        canvas_user_name=row.get("canvas_user_name"),
        connected_at=row.get("connected_at"),
    )


def save(user_id: UserId, base_url: str, token: str, canvas_user_name: Optional[str]) -> CanvasConnection:
    now = datetime.now(timezone.utc).isoformat()
    _get_store().put(user_id, {
        "base_url": base_url,
        "token_ciphertext": _encrypt(token),
        "canvas_user_name": canvas_user_name,
        "connected_at": now,
    })
    return CanvasConnection(base_url, token, canvas_user_name, now)


def delete(user_id: UserId) -> None:
    _get_store().delete(user_id)


def is_connected(user_id: Optional[UserId]) -> bool:
    """For deciding whether to offer the canvas tool this turn. A storage
    failure means no Canvas this turn, never a failed turn."""
    if user_id is None:
        return False
    try:
        return get(user_id) is not None
    except Exception as e:
        print(f"[canvas] connection lookup failed: {e}")
        return False
