
# pip install python-dotenv geocoder requests


# import necessary libraries
import os
import json
from dotenv import load_dotenv
import time
import geocoder
import requests
import json

from pydantic import BaseModel, Field
from typing import List, Optional

# load environment where the API keys are stored
load_dotenv()


# define variables
# time
timestamp = time.localtime()

# location
# ideally i would like this to be able to tell what type of location it is, ie education, business, commercial, residential etc
def check_location():
    # https://stackoverflow.com/questions/24906833/how-to-access-current-location-of-any-user-using-python accessed 07/07/2026
    # https://medium.com/@asir9637/location-tracking-made-easy-python-and-gps-coordinates-7966fb6557c4 accessed 07/07/2026
    try: 
        location = geocoder.ip('me') # debug: IP isn't necessarily accurate description of location. will need to work on this
        lat, long = location.latlng
        print(location, lat, long)
        return location, lat, long
    except:
        location = 'unknown' # debug investigate the rate limit being hit
        print(location)
        lat = None
        long = None
        return location, lat, long
location,lat,long = check_location()
#gmaps = googlemaps.Client(key='Add Your Key here') # https://github.com/googlemaps/google-maps-services-python
#address_descriptor_result = gmaps.reverse_geocode(location_lat_long, enable_address_descriptor=True)

# weather https://max-coding.medium.com/create-a-weather-map-using-openweather-api-in-python-f048473ca6ae
def check_weather():
    if lat == None:
        return 'unknown'

    openweathermap_api_key = os.environ.get("openweathermap_api_key")
    if not openweathermap_api_key:
        print("<[WARN] OpenWeatherMap - no api key found in environment (openweathermap_api_key)>")
        return 'unknown'

    owm_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={long}&appid={openweathermap_api_key}"
    try:
        owm_response = requests.get(owm_url, timeout=10)
        owm_response_json = owm_response.json()
    except requests.RequestException as e:
        print(f"<[WARN] OpenWeatherMap - request failed: {e}>")
        return 'unknown'

    # OWM signals errors with a non-200 'cod' and a 'message' instead of weather data
    if owm_response.status_code != 200 or "main" not in owm_response_json:
        print(f"<[WARN] OpenWeatherMap - {owm_response_json.get('cod', owm_response.status_code)}: "
              f"{owm_response_json.get('message', 'unexpected response')}>")
        return 'unknown'

    return {
        "temp": owm_response_json["main"]["temp"] - 273.15, # convert to celsius
        "description": owm_response_json["weather"][0]["description"],
        "icon": owm_response_json["weather"][0]["icon"]
    }

# define FSM states
class State:
    def __init__(self, name, immediate_context, behavioural_context, physiological_context, user_input, system_input, update_context):
        self.name = name # eg ['init', 'adaptive_interface', 'command_driven_interface', 'default']
        # the following is binary yes no depending on what context and inputs it take
        self.immediate_context = immediate_context
        self.behavioural_context = behavioural_context
        self.physiological_context = physiological_context
        self.user_input = user_input
        self.system_input = system_input
        self.update_context = update_context

# initialise state objects that indicate what inputs and outputs are necessary
init = State('init', 1, 0, 1, 1, 1, 0)
adaptive_interface = State('adaptive_interface', 1, 1, 1, 1, 0, 1)
command_driven_interface = State('command_driven_interface', 1, 0, 0, 0, 1, 1)
default = State('default', 1, 1, 1, 0, 1, 0)

# define schema structure
# https://ai.google.dev/gemini-api/docs/structured-output
class Immediate_context(BaseModel):
    time : str = Field(description = f"HH:MM derived from {time.localtime()}")
    day : str = Field(description = f"Day of the week, DD Month derived from {timestamp}")
    location : str = Field(description = f"location derived from {check_location()}")
    current_events : str = Field(description = "current events derived from calendar, N/A if there's no data")
    future_events : str = Field(description = "events occuring before midnight today, N/A if there's no data")
    weather : str = Field(description = f"temperature, description (derived from {check_weather()})")
    prompt : str = Field(description = f"the user input")
    task : str = Field(description = f"task requested by user, derived from the user input")
    application : str = Field(description=f"application requested by user, derived from the user_input")

class Behavioural_context(BaseModel):
    similar_routines : str = Field(description = "description of previous similar user routines/prompts/behaviours experienced at a similar time and day, N/A if there's no data")
    conflicting_routines : str = Field(description = "description of previous similar user routines/prompts/behaviours experienced at a similar time and day, N/A if there's no data")
    previous_corrections : str = Field(description = "previous user corrections from similar prompts, tasks or applications, N/A if there's no data")
                                   
class Physiological_context(BaseModel):
    input_type : str = Field(description = "text OR voice OR silent speech OR buttons OR system")
    input_tone : str = Field(description = "make an educated guess if input type is text OR voice, neutral is a valid answer")
    heart_rate : int = Field(description = "data from external device, N/A if there's no data")
    gaze : str = Field(description = "description of attentiveness using data from external device, N/A if there's no data")
    posture : str = Field(description = "description of posture using data from external device, N/A if there's no data")

class Context(BaseModel):
    immediate_context : Immediate_context
    behavioural_context : Behavioural_context  
    physiological_context : Physiological_context


def change_state(user_input):
    if user_input == '/reset':
        print("Are you sure you want to permanently erase your data? You cannot undo this action.")
        current_state = init
        return current_state
    elif user_input == '/user_override':
        print("You are switching to a command-driven interface.")
        current_state = command_driven_interface
        return current_state
    elif user_input == '/power_on':
        current_state = default
        return current_state
    elif user_input == '/current_status':
        current_state = default
        return current_state
    else:
        current_state = adaptive_interface
        return current_state

# defining persistent memory with SQlite
# https://pythonforthelab.com/blog/storing-data-with-sqlite/


def gather_context(user_input):
    model = "qwen3.5:4b"                   

    system_instruction = f"""You are the AI assistant for a new novel personal computing device that manages attention, autonomy and privacy. Please write with Australian English spelling. Assume the timezone is Sydney Australian time. 

The user will ask for questions and information relating to oral functions on a mobile phone. Your job is to gather context. This is done through layers of information as seen below. 
    1. Immediate context. What app are they asking for? What task are they asking for? What time of day are they asking and where are they asking from?
    2. Behavioural information. (the users routines, past information and previous corrections)
    3. physiological signals (take inputs from electronic components If available that provide information about the user's posture, gaze and voice tone)

Why does this matter? 
Your outputs will be fed into another LLM client that looks at the current context and infers intent. 

- Determine if we have enough information to provide a response.
    - If yes, provide a description of the context gathered, and generate a brief JSON description of the response.
    - If not, ask for additional information
- Return a response as defined by the response schema. Do not output anything else. 

Rules:
- Never make up information. If you don't know information, provide an N/A response. 
- Write with Australian English spelling

"""

    response = requests.post(
        #"http://localhost:11434/api/chat",
        "http://100.72.108.91:11434/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_input},
            ],
            "format":Context.model_json_schema(),
            "think": False,
            "stream": True,
            "options": {"num_ctx": 16384},
        },
        stream=True,
    )
    buffer = ""
    for chunk in response.iter_lines():
        if chunk and (text := json.loads(chunk).get("message", {}).get("content")):
            print(text, end="")
            buffer += text

    result = Context.model_validate_json(buffer)
    return result        

# main loop
if __name__ == "__main__":
    print("Hello!")
    while True:
        user_input = input()
        current_state = change_state(user_input)
        print(f"The current state is {current_state.name}")
        gather_context(user_input)