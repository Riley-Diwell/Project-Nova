"""
schemas/base_context.py - Section 5.2: State Estimator  (Georgia)

STATUS: wip

WHAT THIS FILE IS
Defines schemas for base context. This is the minimum amount of context that must be included 
with every event change (passed through to the state estimator). 
    1. Uses Pydantic BaseModel for validation and JSON (de)serialisation.

WHO USES THIS
- Riley: POSTs data using this schema
- Georgia: state_estimator.py uses these events as input to estimate state

"""

# import important libraries
from datetime import datetime
from pydantic import BaseModel

# define events
# define system events
class BaseContext(BaseModel):
    """
    This includes basic context that should be appended to any event. 
    """
    # basic info
    timestamp: datetime
    # location
    lat : float
    lng : float
    # screen status
    screen_status: bool
    # accelerometer
    threshold: str

