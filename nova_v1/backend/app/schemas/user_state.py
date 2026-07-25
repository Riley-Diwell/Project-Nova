"""
schemas/user_state.py - Section 5.2: State Estimator  (Georgia)

WHAT THIS FILE IS
Defines schemas for the current user state. 
    1. Uses Pydantic BaseModel for validation and JSON (de)serialisation.

WHO USES THIS
- Georgia: state_estimator.py uses these schemas to store user state

"""

# import important libraries
from datetime import datetime
from typing import Annotated, Literal, Union
from uuid import UUID
from pydantic import BaseModel, Discriminator, Field

# create user state input schema
class UserStateInput(BaseModel):
    """
    debug fill this in later
    """
    id: UUID
    timestamp: datetime
    location : str
    screen : Literal["on", "off", "unknown"]
    calendar: Literal["free", "in_event", "event_starting_soon"]
    accelerometer: Literal["stationary", "walking", "running", "cycling", "driving"]
    #state_confidence: float # 0-1
    # debug note: should this be a combination of confidence of all data, or just the inferred state?
    #predicted_next_state : 

# create user state output schema
class UserStateOutput(BaseModel):
    """
    debug fill this in later
    """
    id: UUID
    timestamp: datetime
    location : str
    screen : Literal["on", "off", "unknown"]
    calendar: Literal["free", "in_event", "event_starting_soon"]
    accelerometer: Literal["stationary", "walking", "running", "cycling", "driving"]
    inferred_user_state: str
    state_confidence: float # 0-1
    # debug note: should this be a combination of confidence of all data, or just the inferred state?
    #predicted_next_state : 


# create user state output schema
class NextUserStateOutput(BaseModel):
    """
    debug fill this in later
    """
    id: UUID
    timestamp: datetime
    location : str
    screen : Literal["on", "off", "unknown"]
    calendar: Literal["free", "in_event", "event_starting_soon"]
    accelerometer: Literal["stationary", "walking", "running", "cycling", "driving"]
    inferred_user_state: str
    state_confidence: float # 0-1
    inferred_next_user_state: str
    next_state_confidence: float #0-1
    # debug note: should this be a combination of confidence of all data, or just the inferred state?
    #predicted_next_state :     