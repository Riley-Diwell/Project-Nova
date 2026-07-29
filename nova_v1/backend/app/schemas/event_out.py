"""
schemas/event_out.py - Section 5.3: Intent Surface  (Georgia)

STATUS: working draft

WHAT THIS FILE IS
Response schema for POST /event — what the backend hands back to Riley's
Android client for every event it receives.

WHO USES THIS
- Georgia: main.py computes this using the intent surface loop
- Riley: receives this and handles it via Android
"""

from typing import Any, Literal, Union
from uuid import UUID
from pydantic import BaseModel


class EventOut(BaseModel):
    status: Literal["final"] = "final"
    event_id: UUID
    speech: str
    actions: list[dict[str, Any]] = []


class NeedMoreOut(BaseModel):
    """
    Returned from /event or /event/continue when the Intent Surface needs
    data that only exists on the device (e.g. a calendar range outside the
    UserState snapshot) before it can finish answering. Android is expected
    to resolve `request` on-device and POST the result to /event/continue
    with this same session_id.
    """
    status: Literal["need_more"] = "need_more"
    event_id: UUID
    session_id: str
    request: dict[str, Any]


EventResponse = Union[EventOut, NeedMoreOut]

