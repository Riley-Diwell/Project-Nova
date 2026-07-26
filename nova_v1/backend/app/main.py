"""
backend/app/main.py - Section 5.3: Intent Surface  (Georgia)

STATUS: wip

WHAT THIS FILE IS
FastAPI entrypoint. Exposes POST /event as the single wire seam between
Riley's Android client and the backend.
    1. Currently returns a canned EventOut.
    2. 

Run:
    cd backend/app && uvicorn main:app --reload

WHO USES THIS
- Riley: POSTs events here from Android
- Georgia: 
"""

# import necessary libraries
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import FastAPI

# import nova libraries
from schemas.event import Event
from schemas.event_out import EventOut
from schemas.user_state import UserState

# initialise app
app = FastAPI(title="NOVA V1")

# this is how I receive events from Riley
# app.post handles incoming HTTP POST requests
@app.post("/event", response_model=EventOut)
async def receive_event(event: Event) -> EventOut:
    placeholder_state = UserState(
        id=uuid4(),
        timestamp=datetime.now(timezone.utc),
        activity="stationary",
        location_ctx="unknown",
        calendar_ctx="free",
        dnd=False,
        screen="unknown",
        current_state="placeholder",
        confidence=0.0,
        predicted_next_state=None,
    )
    return EventOut(
        event_id=event.id,
        speech="",
        actions=[],
        user_state=placeholder_state,
    )
