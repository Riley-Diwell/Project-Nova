"""
backend/app/main.py - Section 5.3: Intent Surface  (Georgia)

STATUS: wip

WHAT THIS FILE IS
FastAPI entrypoint. Exposes POST /event as the single wire seam between
Riley's Android client and the backend.

Android computes UserState on-device (UserStateCollector.kt, ~19 fused
signals) and posts it directly alongside every Event - the backend does not
re-derive state from raw signals, so there is no separate Signals payload.

Run:
    cd backend/app && uvicorn main:app --reload

WHO USES THIS
- Riley: POSTs events here from Android
- Georgia:
"""

# import necessary libraries
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# import nova libraries
from schemas.event import Event
from schemas.event_out import EventOut, EventResponse, NeedMoreOut
from schemas.user_state import UserState
from intent_surface import loop
from intent_surface.loop import IntentResult, NeedMoreResult
from tools.notification_batcher import NotificationBatcher
from tools.notification_management import register_batcher


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Shared with the notification_management tool (see intent_surface/loop.py's
    # ToolRegistry) - without this, the tool has no batcher to query.
    batcher = NotificationBatcher()
    batcher.start()
    register_batcher(batcher)
    yield
    batcher.stop()


# initialise app
app = FastAPI(title="NOVA V1", lifespan=_lifespan)

# combine event and user_state into one wrapper - matches what Android posts
class InputWrapper(BaseModel):
    event: Event
    user_state: UserState

# what Android posts back to /event/continue once it has resolved a
# need_more request on-device (e.g. queried the calendar for the requested
# range). result is whatever shape the request type calls for - opaque here,
# loop.resume() feeds it straight back to Claude as the paused tool's result.
class ContinueWrapper(BaseModel):
    session_id: str
    result: Any


def _to_response(intent: IntentResult | NeedMoreResult) -> EventResponse:
    if isinstance(intent, NeedMoreResult):
        return NeedMoreOut(
            event_id=intent.event_id,
            session_id=intent.session_id,
            request={
                "type": intent.request_type,
                "from": intent.from_time,
                "to": intent.to_time,
            },
        )
    return EventOut(event_id=intent.event_id, speech=intent.speech, actions=intent.actions)


# this is how I receive events from Riley
# app.post handles incoming HTTP POST requests
@app.post("/event", response_model=EventResponse)
async def receive_event(input_wrapper: InputWrapper) -> EventResponse:
    print(f"[/event] received text: {getattr(input_wrapper.event, 'text', None)!r}")
    us = input_wrapper.user_state
    print(f"[/event] calendar_ctx={us.calendar_ctx!r} "
          f"current_events={len(us.current_events)} upcoming_events={len(us.upcoming_events)}")
    intent = loop.run(input_wrapper.user_state, input_wrapper.event)
    return _to_response(intent)


# Android posts here after resolving a need_more request from /event (see
# loop.py's CLIENT_TOOLS) - resumes the same paused Claude conversation.
@app.post("/event/continue", response_model=EventResponse)
async def continue_event(input_wrapper: ContinueWrapper) -> EventResponse:
    print(f"[/event/continue] session_id={input_wrapper.session_id!r}")
    try:
        intent = loop.resume(input_wrapper.session_id, input_wrapper.result)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _to_response(intent)