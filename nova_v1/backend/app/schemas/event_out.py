"""
schemas/event_out.py - Section 5.3: Intent Surface  (Georgia)

STATUS: wip 

WHAT THIS FILE IS
Response schema for POST /event — what the backend hands back to Riley's
Android client for every event it receives.
    1. 

WHO USES THIS
- 
"""

from uuid import UUID
from pydantic import BaseModel
from .user_state import UserState


class EventOut(BaseModel):
    event_id: UUID
    speech: str

