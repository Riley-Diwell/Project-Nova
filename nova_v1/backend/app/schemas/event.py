"""
schemas/event.py - Section 5.2: State Estimator  (Georgia)

STATUS: wip

WHAT THIS FILE IS
Defines schemas for events. 
    1. Uses Pydantic BaseModel for validation and JSON (de)serialisation.
    2. Uses a discriminated union on `type` so /event can accept any event
       shape through one wire contract.

WHO USES THIS
- Riley: POSTs events observed via Android using this schema
- Georgia: state_estimator.py uses these events as input to estimate state

"""

# import important libraries
from datetime import datetime
from typing import Annotated, Literal, Union
from uuid import UUID
from pydantic import BaseModel, Discriminator, Field

# define events
# define system events
class BaseEvent(BaseModel):
    """
    This is basic information that all event classes have to include:
    an event ID and a timestamp.

    Ambient context (calendar, location, DND, etc.) is NOT attached here —
    it will arrive via a separate SignalSnapshot alongside the event.
    """
    id: UUID
    timestamp: datetime

class TimeEvent(BaseEvent):
    """
    This event is triggered when the time since the last TimeEvent exceeds 10 minutes. 
    """
    type: Literal["timestamp"] = "timestamp"
    timestamp: datetime

class LocationEvent(BaseEvent):
    """
    This event is triggered when the user's location changes by 1km. 
    """
    type: Literal["location"] = "location"
    lat: float
    lng: float

class NotificationEvent(BaseEvent):
    """
    This event is triggered when a new notification is received. 
    """
    type: Literal["notification"] = "notification"
    app: str
    title: str
    body: str

class CalendarTriggerEvent(BaseEvent):
    """
    This event is triggered when there are any edits in the linked 
    calendar (eg new event added, event edited etc). 
    """
    type: Literal["calendar_trigger"] = "calendar_trigger"
    calendar_event_id: str
    calendar_event_name: str
    calendar_event_duration: float # in hours
    calendar_event_start: datetime # start datetime
    calendar_event_end: datetime # end datetime
    calendar_event_location: str # if location known

class AccelerometerEvent(BaseEvent):
    """
    This event is triggered when the user changes a threshold in speed,
    measured by the acceleromter on the Android. 
    The thresholds are:
    - stationary
    - walking
    - running
    - cycling
    - driving
    """
    type: Literal["accelerometer"] = "accelerometer"
    threshold: str  # theshold name, eg walking, running etc

class ScreenEvent(BaseEvent):
    """
    This event is triggered when the status of the phone screen changes. Statuses could be:
    - on
    - off
    - unknown
    """
    type: Literal["screen"] = "screen"
    status: bool

# define user events
# in the future can add in button event
class STTEvent(BaseEvent):
    """
    This event is triggered when the Nova System receives a speech-to-text (STT) input. 
    """
    type: Literal["STT"] = "STT"
    text: str                           


# combine into a list 
events = [
    TimeEvent,
    LocationEvent,
    NotificationEvent,
    CalendarTriggerEvent,
    AccelerometerEvent,
    ScreenEvent,
    STTEvent
]

Event = Annotated[
    Union[tuple(events)],
    Discriminator("type"),
]