"""
tools/georgias_tools.py - Section 5.2: State Estimator (Georgia), Section 5.3: Intent Surface (Georgia)

STATUS: wip

WHAT THIS FILE IS
A dump of tools that Georgia needs. Keeping on a separate server for now, will integrate later. 
1. Uses FastMCP
2. Adheres to Naoise's tool schema

WHO USES THIS
- Naoise: MCP interface, gain
- Georgia: State estimator, intent surface

"""

# import necessary libraries and set up environment
from typing import Any
from fastmcp import FastMCP
import requests
import os
from dotenv import load_dotenv
import asyncio
load_dotenv()

import googlemaps
gmaps = googlemaps.Client(key=os.environ.get('google_maps_api_key'))

# debug add this stuff in when Naoise has finalised this
'''from ..base import BaseTool


class DemoTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="demo_echo",
            description="Echoes whatever input it's given. Reference tool only — not a real function.",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        return {"echoed": tool_input}
        '''

# create an MCP server
mcp = FastMCP("Context Server")

# create contextual tools
@mcp.tool()
async def check_weather(lat: float, long: float) -> dict:
    """ Checks the weather depending on the current location. 
    Use when the user's query depends on their location, activity planning or outfit planning. """
    try:
        openweathermap_api_key = os.environ.get("openweathermap_api_key")
        owm_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={long}&appid={openweathermap_api_key}"
        owm_response = requests.get(owm_url)
        owm_response_json = owm_response.json()
        weather = {
            "temp": owm_response_json["main"]["temp"] - 273.15, # convert to celsius
            "description": owm_response_json["weather"][0]["description"],
            "icon": owm_response_json["weather"][0]["icon"]
        }
    except Exception as e:
        print({"error": str(e)})
        weather = 'N/A'
    return weather

# location
@mcp.tool()
async def get_address(lat: float, long: float) -> str:
    """Uses lat and long coordinates to reverse geocode an address result. 
    Use when context around location is required. """
    reverse_geocode_result = gmaps.reverse_geocode((lat, long))
    return reverse_geocode_result

# run the server
if __name__ == "__main__":
    # run with HTTP transport
    mcp.run(transport="http", host="localhost", port=8000, stateless_http=True)


