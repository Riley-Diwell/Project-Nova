# import necessary libraries
from fastmcp import FastMCP
from datetime import datetime
import requests
import geocoder
import os
import json
import asyncio

# create an MCP server
mcp = FastMCP("Context Server")



# immediate context tools
@mcp.tool(tags={"immediate"})
async def get_time() -> dict:
    """Current local time and day. Call when the user's query depends on time of day, day of week, or scheduling."""
    now = datetime.now()
    return {"time": now.strftime("%H:%M"), "day": now.strftime("%A %d %B")} # transform datetime object into a formatted string


@mcp.tool(tags={"immediate"})
async def check_location() -> tuple:
    """Checks the general location based on the IP address. Call when the user's query depends on their location or general immediate context."""

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


@mcp.tool(tags={"immediate"})
async def check_weather(lat: float, long: float) -> dict:
    """ Checks the weather depending on the current location. Check when the user's query depends on their location, activity planning or outfit planning. """
    if lat != None:
        openweathermap_api_key = os.environ.get("openweathermap_api_key")
        owm_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={long}&appid={openweathermap_api_key}"
        owm_response = requests.get(owm_url)
        owm_response_json = owm_response.json()
        weather = {
            "temp": owm_response_json["main"]["temp"] - 273.15, # convert to celsius
            "description": owm_response_json["weather"][0]["description"],
            "icon": owm_response_json["weather"][0]["icon"]
        }
    else:
        weather = 'unknown'
    return weather

# behavioural context tools
@mcp.tool(tags={"behavioural"}) 
async def update_user_persona(user_persona) -> str:
    """The user persona is 3-sentence maximum description of who the user is perceived to be. 
    Core habits, commitments and personality traits should be documented here. This tool should be 
    called when experiencing /user_override, or when new core patterns about the user is identified."""

    default_user_persona = """Assume the user is an undergraduate university student. The user may spend around 40 hours per week on univeristy tasks,
and may also balance this with a part-time role in the workforce. The user has identified concerns around managing their attention,
and the user may have a reactive rather than proactive attitude towards privacy. """

    model = "qwen3.5:4b"                   

    system_instruction = f"""You are the AI assistant for a new novel personal computing device that manages attention, autonomy and privacy. Please write with Australian English spelling. 

    What is your job?
    1. Analyse immediate, behavioural and physiological data, if available. 
    2. Identify if there are any new core habits, committments or personality traits that differ from the current user persona. 
    If so, please rewrite the user persona to include core insights. 
        For example, if the user has shown signs of being a night owl, include this information in the user persona. 

    Rules:
    1. Never assume, if you are uncertain do not make any changes. 
    2. Be concise, the user persona should be 3 sentences long at a maximum. 
    3. If there is no current user persona recorded, use the {default_user_persona}

"""

    user_persona = requests.post(
        #"http://localhost:11434/api/chat",
        "http://100.72.108.91:11434/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_input},
            ],
            "think": False,
            "stream": True,
            "options": {"num_ctx": 16384},
        },
        stream=True,
    )
    buffer = ""
    for chunk in user_persona.iter_lines():
        if chunk and (text := json.loads(chunk).get("message", {}).get("content")):
            print(text, end="")
            buffer += text

    return user_persona 



# physiological context tools#  


# run the server
if __name__ == "__main__":
    # run with HTTP transport
    mcp.run(transport="http", host="localhost", port=8000)
