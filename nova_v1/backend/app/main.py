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
from typing import Any, AsyncIterator, Literal
from uuid import UUID

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
import persona


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
def _open_episode(event: Event, user_state: UserState) -> str | None:
    try:
        episode_id = memory.append({
            "event_type": event.type,
            "event": event.model_dump(mode="json"),
            "user_state": user_state.model_dump(mode="json"),
        })
        print(f"[memory] opened episode {episode_id} (event_type={event.type!r})")
        return episode_id
    except Exception as e:
        print(f"[memory] append skipped: {e}")
        return None

# log that looked like feedback and was not.
def _close_episode(intent: IntentResult) -> None:
    if not intent.episode_id:
        return
    try:
        memory.close(intent.episode_id, action={
            "actions": intent.actions,
            "speech": intent.speech,
        })
        print(f"[memory] closed episode {intent.episode_id} "
              f"({len(intent.actions)} action(s))")
    except Exception as e:
        print(f"[memory] episode close skipped: {e}")


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
    return EventOut(
        event_id=intent.event_id,
        speech=intent.speech,
        actions=intent.actions,
        episode_id=intent.episode_id,
    )


# this is how I receive events from Riley
# app.post handles incoming HTTP POST requests
@app.post("/event", response_model=EventResponse)
async def receive_event(input_wrapper: InputWrapper) -> EventResponse:
    print(f"[/event] received text: {getattr(input_wrapper.event, 'text', None)!r}")
    us = input_wrapper.user_state
    print(f"[/event] calendar_ctx={us.calendar_ctx!r} "
          f"current_events={len(us.current_events)} upcoming_events={len(us.upcoming_events)}")
    episode_id = _open_episode(input_wrapper.event, us)
    intent = loop.run(input_wrapper.user_state, input_wrapper.event, episode_id)
    if isinstance(intent, IntentResult):
        _close_episode(intent)
    return _to_response(intent)


# --- the turn's Outcome (Sections 5.5, 5.7) ----------------------------------
# The reinforcement signal, and the reason episodic_memory.outcome exists.
#
# The phone reports how the turn ended: `accepted` when TTS ran to completion,
# `rejected` when the user pressed stop and talked over it. Nothing else is a
# verdict - in particular the gate's own decision is not, which is what an
# earlier draft wrongly wrote into this column.
#
# Barge-in is the only negative signal V1 collects, deliberately: a thumbs-down
# button would be a phone interaction, and removing phone interactions is the
# entire product. Stop is a control the user wants for its own sake, so reading
# it as feedback costs them nothing. See docs/adr/0002.

class OutcomeIn(BaseModel):
    episode_id: str
    outcome: Literal["accepted", "rejected"]


@app.post("/event/outcome", status_code=204)
async def report_outcome(report: OutcomeIn) -> None:
    """Record the user's verdict on a turn and move the gain of what it did."""
    try:
        memory.close(report.episode_id, outcome=report.outcome)
    except Exception as e:
        # Non-fatal like every other Memory touch, but the reinforcement below
        # still runs: the gain move is the part the user will actually notice.
        print(f"[memory] outcome write skipped: {e}")

    moved = loop.reinforce_episode(report.episode_id, report.outcome)
    print(f"[gain] episode {report.episode_id} {report.outcome}: moved {moved}")


# --- per-tool gain (Section 5.7) ---------------------------------------------
# The Android app's Gain tab (ui/screens/GainScreen.kt) reads these to draw one
# dial per Function tool and writes back what the user dials in. Gain is both
# user-set here and moved by reinforcement above; an override, while present,
# wins over whatever reinforcement has learned.

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
    # The turn that started at /event may only finish here, so this is the
    # other place an episode can close.
    if isinstance(intent, IntentResult):
        _close_episode(intent)
    return _to_response(intent)