"""
state_estimator/state_estimator_v2.py - Section 5.2: State Estimator  (Georgia)

STATUS: wip

WHAT THIS FILE IS
Map event and signals into a user state schema. 
1. 

WHO USES THIS
- 

"""

# import necessary libraries
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

    UserState.timestamp = Signals.timestamp
    UserState.screen=Signals.screen_status
    UserState.dnd = Signals.dnd

    UserState.Activity = Signals.accelerometer # debug check this
    UserState.location_ctx= gmaps.reverse_geocode((Signals.lat, Signals.long)) # map to address
  
    
    UserState.calendar_ctx= CalendarCtx

    return UserState

def get_confidence(UserState):
    """
    Very simple confidence scoring system. Confidence is determined on how much data is actually available. 
    Validity of the data is out of scope from this function. 
    """
    sum = 0
    total = 0
    for field_name, field_info in UserState.model_fields.items():
        if not field_info: # if missing information
            total += 1
        elif type(field_info) == str:
            if field_info == "unknown":
                total += 1
            else:
                sum += 1
                total += 1
        elif field_name=="confidence": # skip this one
            break
        else:
            sum += 1
            total += 1
    confidence = sum/total
    return confidence        
            