"""
api/ack.py - the line said while a turn runs  (Riley)

    POST /event/ack   {text} -> {ack}   (Bearer)

The phone sends the transcript it is also sending to /event, and speaks the
reply's `ack` if /event hasn't answered after a moment. `ack` is null for
anything that isn't a request. See app/control/ack.py.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.control import ack
from app.core.auth import AuthUser, current_user

router = APIRouter(prefix="/event", tags=["event"])


class AckIn(BaseModel):
    text: str


class AckOut(BaseModel):
    ack: Optional[str] = None


@router.post("/ack", response_model=AckOut)
def acknowledge(body: AckIn, user: AuthUser = Depends(current_user)) -> AckOut:
    return AckOut(ack=ack.phrase(body.text, user.id))
