"""
core/request_user.py - the signed-in user, for code a route can't hand it to

Routes that need to know who is calling take `Depends(current_user)` and pass
the user down (see api/notes.py). Function tools can't: they run deep inside
the Intent Surface's loop behind POST /event, and threading a user through
intent_surface -> dispatcher -> registry -> every tool would change seams that
belong to other owners. So main.py binds the verified user for the whole
request here, and a tool that needs it (the memory tool, for the user's notes)
reads request_user_id().

The id only ever comes from a verified token (core/auth.py), never from a
request body or a tool's input.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional
from uuid import UUID

from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool

from app.core.auth import PUBLIC_ROUTES, bearer_token, current_user

_user_id: ContextVar[Optional[UUID]] = ContextVar("nova_request_user_id", default=None)


async def bind_request_user(request: Request, token: Optional[str] = Depends(bearer_token)) -> None:
    """App-wide dependency (main.py). Async on purpose: a sync dependency runs in a
    worker thread, and a ContextVar set there never reaches the endpoint."""
    route = request.scope.get("route")
    if getattr(route, "path", None) in PUBLIC_ROUTES:
        return
    user = await run_in_threadpool(current_user, token)  # may fetch JWKS - keep it off the loop
    _user_id.set(user.id)


def request_user_id() -> Optional[UUID]:
    """The user this request is for, or None outside a signed-in request."""
    return _user_id.get()


@contextmanager
def as_user(user_id: Optional[UUID]) -> Iterator[None]:
    """Run a block as [user_id] - tests, and any future non-request caller."""
    token = _user_id.set(user_id)
    try:
        yield
    finally:
        _user_id.reset(token)
