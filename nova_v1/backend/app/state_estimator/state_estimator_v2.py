"""
state_estimator/state_estimator_v2.py - Section 5.2: State Estimator  (Georgia)

STATUS: superseded

WHAT THIS FILE IS
Map event and signals into a user state schema, calculates confidence score. 


WHO USES THIS
- N/A

"""

# run uvicorn main:app --reload in terminal

# import necessary libraries
from uuid import uuid4
import os
from dotenv import load_dotenv
load_dotenv()
import googlemaps
gmaps = googlemaps.Client(key=os.environ.get('google_maps_api_key'))

# import nova libraries
from schemas.user_state import UserState

def state_mapping(Signals):
    """
    Map data from signals into a user state. This is currently a very simple mapping. 
    """

    state = UserState(  id = uuid4(),
                        timestamp = Signals.timestamp,
                        screen=Signals.screen_status,
                        dnd=Signals.dnd,
                        activity = Signals.accelerometer,
                        location_ctx= [Signals.lat, Signals.long],
                        calendar_ctx= "unknown",
                        confidence=0.0 # placeholder
                        )
    state.confidence = get_confidence(state)
    return state

def get_confidence(user_state: UserState):
    """
    Very simple confidence scoring system. Confidence is determined on how much data is actually available. 
    Validity of the data is out of scope from this function. 
    """
    sum = 0
    total = 0
    data = user_state.model_dump()

    for name, value in data.items():
        if value is None: # if missing information
            total += 1
        elif type(value) == str:
            if value == "unknown":
                total += 1
            else:
                sum += 1
                total += 1
        elif name=="confidence": # skip this one
            continue
        else:
            sum += 1
            total += 1
    if total > 0:
        confidence = sum/total
    else: 
        confidence = 0
    return confidence        
            