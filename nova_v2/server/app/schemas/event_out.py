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
    episode_id: str | None = None
    # Set only when speech leaves a question dangling (intent_surface.py's
    # _classify_confirmation): "yes_no" if Android should offer Yes/No quick
    # replies alongside its usual text/voice input, "open" if it's a question
    # but not one a Yes/No answer fits, None otherwise.
    confirmation: Literal["yes_no", "open"] | None = None
    # {destination, mode, leave_in_minutes, minutes_until_start,
    # event_title, destination_lat, destination_lng} when
    # navigation_departure_time ran this turn and could
    # measure a countdown - see intent_surface.py's
    # TurnContext.scheduled_departure. Android schedules a precise local
    # alarm against leave_in_minutes rather than waiting for the next
    # ambient poll, and builds the leave-soon notification text itself from
    # these fields - the model's speech is never used for it (see
    # AmbientCheckRunner.kt).
    scheduled_departure: dict[str, Any] | None = None
    # {destination, event_title, minutes_until_start, reason} when
    # navigation_departure_time ran this turn but could not measure a travel
    # time - see intent_surface.py's TurnContext.departure_unknown. On an
    # ambient check Android notifies the user that it couldn't work out when
    # to leave, again in its own fixed wording.
    departure_unknown: dict[str, Any] | None = None
    # True when enough has happened since NOVA last learned from this user's
    # activity (store/consolidation.due). The phone then asks for the pass in
    # the background (POST /persona/consolidate?if_due=true) - a request it
    # makes keeps its CPU on Cloud Run, where work the server started after
    # replying may never finish.
    consolidation_due: bool = False
    # Notes whose words this turn holds without an Action naming them (the
    # "yes" to a reminder offer - intent_surface's TurnContext.from_notes). The
    # phone tags the turn's Voice history bubbles with them, so deleting the
    # note deletes those bubbles too.
    note_ids: list[str] = []


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

