"""
backend/app/main.py - Section 5.3: Intent Surface  (Georgia)

STATUS: wip

WHAT THIS FILE IS
FastAPI entrypoint. Exposes POST /event as the single wire seam between
Riley's Android client and the backend.
    1. 

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
from schemas.signals import Signals
from schemas.user_state import UserState
from intent_surface import loop
from state_estimator import state_mapping

# initialise app
app = FastAPI(title="NOVA V1")

# this is how I receive events, signals from Riley
# app.post handles incoming HTTP POST requests
@app.post("/event", response_model=EventOut)
async def receive_event(event, signals) -> EventOut: # event:Event, signals:Signals
    state = state_mapping(signals)
    intent = loop.run(state, event, signals)
    return EventOut(
        event_id=event.id,
        speech=intent.speech,
        actions=intent.actions,
        user_state=state,
    )