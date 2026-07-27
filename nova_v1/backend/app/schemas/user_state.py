"""
schemas/user_state.py - Section 5.2: State Estimator  (Georgia)

STATUS: working draft

WHAT THIS FILE IS
Defines the UserState schema — the state estimator's output and the intent
surface's input. One flat model with a nested NextStatePrediction so callers
never have to decide "which of the three UserState classes do I want".
    1. Uses Pydantic BaseModel for validation and JSON (de)serialisation.

WHO USES THIS
- Georgia: state_estimator writes UserState; intent_surface reads it

"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field


Activity = Literal["stationary", "walking", "running", "cycling", "driving", "unknown"]
Screen = Literal["on", "off", "unknown"]
CalendarCtx = Literal["free", "in_event", "event_starting_soon", "unknown"]

class UserState(BaseModel):
    """
    Deterministic summary of user state, based on event context and android signals context. 
    """
    id: UUID
    timestamp: datetime
    activity: Activity
    location_ctx: str
    calendar_ctx: CalendarCtx
    dnd: bool
    screen: Screen
    confidence: float = Field(ge=0.0, le=1.0)
    
