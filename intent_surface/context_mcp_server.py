# import necessary libraries
from fastmcp import FastMCP
from datetime import datetime
import requests
import geocoder
import os
import asyncio

# create an MCP server
mcp = FastMCP("Context Server")

# create tools
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


# physiological context tools#  

# run the server
if __name__ == "__main__":
    #mcp.run()
    # Run with HTTP transport
    mcp.run(transport="http", host="localhost", port=8000)
