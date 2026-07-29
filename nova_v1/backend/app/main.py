"""
backend/app/main.py - Section 5.3: Intent Surface  (Georgia)

STATUS: wip

WHAT THIS FILE IS
FastAPI entrypoint. Exposes POST /event as the single wire seam between
Riley's Android client and the backend.

Android computes UserState on-device (UserStateCollector.kt, ~19 fused
signals) and posts it directly alongside every Event - the backend does not
re-derive state from raw signals, so there is no separate Signals payload.
That makes /event the first place UserState exists backend-side, so this is
where each episode is appended to the Memory log (see _log_episode).

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
from schemas.tool_gain import ToolGainOut, ToolGainUpdate
from schemas.user_state import UserState
from intent_surface import loop
from intent_surface.loop import IntentResult, NeedMoreResult
from tools.notification_batcher import NotificationBatcher
from tools.notification_management import register_batcher

import memory


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


# Android determines UserState on-device, so POST /event is where it first
# reaches the backend - i.e. the point the episodic Memory log is meant to
# capture (schema.sql: "one row per event, event + the User State it produced").
# Logged before the loop runs so an episode survives a failing Claude call, and
# so loop.run() can read prior episodes back as context.
# Non-fatal: an unconfigured or unreachable Supabase must not fail /event.
def _log_episode(event: Event, user_state: UserState) -> str | None:
    try:
        row_id = memory.write({
            "event_type": event.type,
            "event": event.model_dump(mode="json"),
            "user_state": user_state.model_dump(mode="json"),
        })
        print(f"[memory] wrote episode {row_id} (event_type={event.type!r})")
        return row_id
    except Exception as e:
        print(f"[memory] write skipped: {e}")
        return None


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
    _log_episode(input_wrapper.event, us)
    intent = loop.run(input_wrapper.user_state, input_wrapper.event)
    return _to_response(intent)


# --- per-tool gain (Section 5.7) ---------------------------------------------
# The Android app's Gain tab (ui/screens/GainScreen.kt) reads these to draw one
# dial per Function tool and writes back what the user dials in. V1 gain is
# user-set (schema.sql), so this is the seam that sets it.

@app.get("/tools/gain", response_model=list[ToolGainOut])
async def get_tool_gains() -> list[dict[str, Any]]:
    return loop.GAIN_OVERRIDES.view_all()


# PUT, not POST: setting a tool's override to x is idempotent. A null override
# clears it, reverting that tool to its learned value.
@app.put("/tools/gain/{tool_name}", response_model=ToolGainOut)
async def put_tool_gain(tool_name: str, update: ToolGainUpdate) -> dict[str, Any]:
    try:
        return loop.GAIN_OVERRIDES.set(tool_name, update.override)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown tool: {tool_name!r}")


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