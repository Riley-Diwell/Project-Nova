"""
schemas/user_state.py - Section 5.2: State Estimator  (Georgia)

STATUS: working draft

WHAT THIS FILE IS
Defines the UserState schema — the state estimator's output and the intent
surface's input. One flat model with a nested NextStatePrediction so callers
never have to decide "which of the three UserState classes do I want".
    1. Uses Pydantic BaseModel for validation and JSON (de)serialisation.
    2. Field names track plan §5.2 (activity, location_ctx, calendar_ctx,
       dnd, screen, timestamp, confidence, predicted_next_state).

WHO USES THIS
- Georgia: state_estimator writes UserState; intent_surface reads it
- Riley: reads UserState off /event responses for debugging

"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field


Activity = Literal["stationary", "walking", "running", "cycling", "driving"]
Screen = Literal["on", "off", "unknown"]
CalendarCtx = Literal["free", "in_event", "event_starting_soon"]


class NextStatePrediction(BaseModel):
    """
    Forecast of the user's state within roughly the next hour.
    Mirrors UserState's inferrable fields (no recursion) plus `eta_minutes`
    so downstream code can act on the "when" without parsing prose.
    """
    activity: Activity
    location_ctx: str
    calendar_ctx: CalendarCtx
    dnd: bool
    screen: Screen
    eta_minutes: int = Field(ge=0, le=60)
    confidence: float = Field(ge=0.0, le=1.0)


class UserState(BaseModel):
    """
    The fused, per-tick view of what the user is doing right now.
    """
    id: UUID
    timestamp: datetime
    activity: Activity
    location_ctx: str
    calendar_ctx: CalendarCtx
    dnd: bool
    screen: Screen
    current_state: str 
    confidence: float = Field(ge=0.0, le=1.0)
    predicted_next_state: Optional[NextStatePrediction] = None
