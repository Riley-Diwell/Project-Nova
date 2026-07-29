"""
schemas/event.py - Section 5.2: State Estimator  (Georgia)

STATUS: working draft

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
from typing import Annotated, Literal, Union, TypeAlias
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
    # debug we will need to talk about this calculation, I understand that accelerometers
    # do not calculate velocity, I understand that they may output x, y, z etc 
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
    """
    type: Literal["screen"] = "screen"
    status: bool

# define user events
# in the future can add in button event
class VoiceEvent(BaseEvent):
    """
    This event is triggered when the Nova System receives a speech-to-text (STT) input.
    type is "voice" to match what the Android client actually posts (NovaApiClient.kt).
    """
    type: Literal["voice"] = "voice"
    text: str

# discriminated union of every event type.
Event: TypeAlias = Annotated[
    Union[
        TimeEvent,
        LocationEvent,
        NotificationEvent,
        CalendarTriggerEvent,
        AccelerometerEvent,
        ScreenEvent,
        VoiceEvent,
    ],
    Discriminator("type"),
]