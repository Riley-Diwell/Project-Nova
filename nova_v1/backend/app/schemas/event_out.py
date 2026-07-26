"""
schemas/event_out.py - Section 5.3: Intent Surface  (Georgia)

STATUS: working draft

WHAT THIS FILE IS
Response schema for POST /event — what the backend hands back to Riley's
Android client for every event it receives.
    1. `speech` — text for the client to speak (empty when NOVA has nothing to say)
    2. `actions` — action identifiers the client should execute (empty for now;
       will become a structured list once B4/B5 wire the intent loop in)
    3. `user_state` — the fused state at this tick, included so Riley can
       eyeball what the estimator inferred without a second endpoint
    4. `event_id` — echoes the incoming event's id so request/response can be
       correlated in the client's logs

WHO USES THIS
- Riley: reads this off POST /event responses
- Georgia: main.py builds it; intent_surface/loop.py will populate speech+actions
"""

from uuid import UUID
from pydantic import BaseModel
from .user_state import UserState


class EventOut(BaseModel):
    event_id: UUID
    speech: str
    actions: list[str]
    user_state: UserState
