
# pip install google-genai


# import necessary libraries
import os
from google import genai
from google.genai import types
from dotenv import load_dotenv
import time
import datetime
import geocoder
import requests

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
    if lat != None:
        openweathermap_api_key = os.environ.get("openweathermap_api_key")
        owm_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={long}&appid={openweathermap_api_key}"
        owm_response = requests.get(owm_url)
        owm_response_json = owm_response.json()
        sunset_utc = datetime.datetime.fromtimestamp(owm_response_json["sys"]["sunset"])
        weather = {
            "temp": owm_response_json["main"]["temp"] - 273.15, # convert to celsius
            "description": owm_response_json["weather"][0]["description"],
            "icon": owm_response_json["weather"][0]["icon"]
        }
    else:
        weather = 'unknown'
    return weather


# define the AI function with the model, thinking level, API key and the system instruction
def generate(user_input):
    client = genai.Client(
       api_key=os.environ.get("GEMINI_API_KEY")
    )

    model = "gemini-3.1-flash-lite"
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=user_input),
            ],
        ),
    ]
    generate_content_config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_level="MEDIUM",
        ),
        system_instruction=[
            types.Part.from_text(text=
                                 
f"""You are the AI assistant for a new novel personal computing device that manages attention, autonomy and privacy. Please write with Australian English spelling. Assume the timezone is Sydney Australian time. 

The user will ask for questions and information relating to oral functions on a mobile phone. Your job is to determine the user’s intent. This is done through layers of information as seen below. 
    1. Immediate context. What app are they asking for? What task are they asking for? What time of day are they asking and where are they asking from?
    2. Behavioural information. (the users routines, past information and previous corrections)
    3. physiological signals (take inputs from electronic components If available that provide information about the user's posture, gaze and voice tone)

If input = /current_status: 
Your job is to return the following outputs. 
Why? The idea is you should be able to infer user intent based off a mixture of immediate context, behavioural context and physiological context without the user having to prompt anything. 
/current_status is a system prompt - for example, imagine you are scheduled to run /current_status every 10 minutes to update the UI. 

- Determine if we have enough information to provide a response.
    - If yes, provide a description of the context gathered, and generate a brief JSON description of the response.
    - If not, ask for additional information
- Return a response in in the following format:

Record the immediate context in the following JSON format (IMPORTANT: All responses MUST be in this JSON format with no additional text or formatting): 

{{
'time': 'string: HH:MM AM/PM (derived from {time.localtime()})',
'today': 'string: day dd month (derived from {time.localtime()})',
'location': 'string: {check_location()}',
'current events': 'string: current event in calendar, else N/A',
'future events': 'string: events occuring before midnight today, else 'N/A',
'weather': 'string: temperature, description (derived from {check_weather()}). N/A is not an acceptable answer.',
'prompt': 'string: 'prompt',
'task': 'string: task requested by user',
'application': 'string: application requested by user'
}}

Record the behavioural context in the following JSON format (IMPORTANT: All responses MUST be in this JSON format with no additional text or formatting): 

{{
'similar user routines at current time': 'string: description of previous similar user routines/prompts/behaviours experienced at HH:MM AM/PM (time derived from {time.localtime()}), else if no data, N/A',
'similar user routines at current day': 'string: description of previous similar user routines/prompts/behaviours experienced during day (day derived from {time.localtime()}), else if no data, N/A',
'conflicting user routines at current time': 'string: description of previous conflicting user routines/prompts/behaviours experienced at HH:MM AM/PM (time derived from {time.localtime()}), else if no data, N/A',
'conflicting user routines at current day': 'string: description of previous conflicting user routines/prompts/behaviours experienced during day (day derived from {time.localtime()}), else if no data, N/A',
'default user routines at current time': 'string: description of previous default user routines/prompts/behaviours experienced at HH:MM AM/PM (time derived from {time.localtime()}), else if no data, N/A',
'default user routines at current day': 'string: description of previous default user routines/prompts/behaviours experienced during day (day derived from {time.localtime()}), else if no data, N/A',
'past information': 'string: description of any relevant past information, else N/A',
'previous corrections': 'string: 'previous user corrections from similar prompts, else N/A'
}}

Record the physiological context in the following JSON format (IMPORTANT: All responses MUST be in this JSON format with no additional text or formatting):

{{
'input type: 'string: text OR voice OR silent speech OR buttons OR system, else N/A',
'input tone': 'string: make an educated guess if input type is text OR voice, neutral is a valid answer. else N/A'
}}
"""
            )
        ],
    )

    for chunk in client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=generate_content_config,
    ):
        if text := chunk.text:
            print(text, end="")

# main loop
if __name__ == "__main__":
    print("Hello!")
    while True:
        user_input = input()
        generate(user_input)