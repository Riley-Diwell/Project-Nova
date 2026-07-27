"""
schemas/signals.py - Section 5.2: State Estimator  (Georgia)

STATUS: wip

WHAT THIS FILE IS
brief description
1. 

WHO USES THIS
- 

"""

# import important libraries
from datetime import datetime
from typing import Annotated, Literal, Union
from uuid import UUID
from pydantic import BaseModel, Discriminator, Field

# define schema to store contextual signals
class Signals(BaseModel):
    """
    """
    id: UUID
    timestamp: datetime

    # environment
    lat:float
    long:float
    light:float # ambient light?
    accelerometer:str # i would like thresholding but can have raw data if needed

    # attention
    num_notifications:int # unread notifications
    dnd:bool
    ringer:bool
    screen_status:bool

    # calendar
    # debug talk about this with riley
    calendar_event_id: str
    calendar_event_name: str
    calendar_event_duration: float # in hours
    calendar_event_start: datetime # start datetime
    calendar_event_end: datetime # end datetime
    calendar_event_location: str # if location known
