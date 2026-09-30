"""
api/canvas.py - connecting the signed-in user's Canvas

    GET    /canvas   -> CanvasStatus                      (Bearer)
    PUT    /canvas   {base_url, token} -> CanvasStatus    checks it with Canvas first
    DELETE /canvas   -> 204

The token is checked against Canvas (GET /api/v1/users/self) before it is
saved, so a typo is caught on the setup screen rather than on the first voice
question. It is never sent back: status carries the address and the Canvas
name only. Storage and encryption are store/canvas.py's.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.core.auth import AuthUser, current_user
from app.store import canvas as canvas_store
from app.tools.canvas.client import CanvasClient, CanvasError, normalise_base_url

router = APIRouter(prefix="/canvas", tags=["canvas"])


class CanvasConnectIn(BaseModel):
    base_url: str = Field(max_length=200)
    token: str = Field(min_length=20, max_length=200)


class CanvasStatus(BaseModel):
    connected: bool
    base_url: Optional[str] = None
    canvas_user_name: Optional[str] = None
    connected_at: Optional[str] = None


def _status(connection: Optional[canvas_store.CanvasConnection]) -> CanvasStatus:
    if connection is None:
        return CanvasStatus(connected=False)
    return CanvasStatus(
        connected=True,
        base_url=connection.base_url,
        canvas_user_name=connection.canvas_user_name,
        connected_at=connection.connected_at,
    )


def _unavailable(e: Exception) -> HTTPException:
    print(f"[canvas] store unavailable: {type(e).__name__}: {e}")
    return HTTPException(status_code=503, detail="Canvas storage unavailable")


@router.get("", response_model=CanvasStatus)
def status(user: AuthUser = Depends(current_user)) -> CanvasStatus:
    try:
        return _status(canvas_store.get(user.id))
    except Exception as e:
        raise _unavailable(e)


@router.put("", response_model=CanvasStatus)
def connect(body: CanvasConnectIn, user: AuthUser = Depends(current_user)) -> CanvasStatus:
    try:
        base_url = normalise_base_url(body.base_url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    token = body.token.strip()
    try:
        me = CanvasClient(base_url, token).me()
    except CanvasError as e:
        if e.status == 401:
            raise HTTPException(status_code=400, detail=(
                "Canvas didn't accept that token. Check you copied all of it, "
                "and that the address is the one you sign in to Canvas at."))
        raise HTTPException(status_code=400, detail=str(e))
    if not isinstance(me, dict) or "id" not in me:
        raise HTTPException(status_code=400, detail=(
            "That address answered, but not like Canvas does. Check the address."))
    try:
        saved = canvas_store.save(user.id, base_url, token, me.get("short_name") or me.get("name"))
    except canvas_store.CanvasNotConfigured:
        raise HTTPException(status_code=503, detail="Canvas isn't set up on this server yet.")
    except Exception as e:
        raise _unavailable(e)
    print(f"[canvas] connected user={user.id} host={base_url}")
    return _status(saved)


@router.delete("", status_code=204)
def disconnect(user: AuthUser = Depends(current_user)) -> Response:
    try:
        canvas_store.delete(user.id)
    except Exception as e:
        raise _unavailable(e)
    return Response(status_code=204)
