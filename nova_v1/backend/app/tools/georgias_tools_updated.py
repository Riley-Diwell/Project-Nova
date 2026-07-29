"""
Context  tools for the State Estimator + Intent Surface

CHANGES
  - check_location_type() is now implemented using maps_client.get_location_type()
  - get_address() and check_weather() unchanged
  - All three now use maps_client for the Google Maps calls so there's
    one place to maintain the API key and error handling

WHO USES 
  - Georgia: State Estimator, Intent Surface
  - Naoise: MCP interface
"""

from typing import Any
from fastmcp import FastMCP
import requests
import os
from dotenv import load_dotenv
load_dotenv()

try:
    from maps_client import get_location_type, reverse_geocode
except ImportError:
    from backend.app.maps_client import get_location_type, reverse_geocode

mcp = FastMCP("Context Server")


@mcp.tool()
async def check_weather(lat: float, long: float) -> dict:
    """Checks the weather depending on the current location.
    Use when the user's query depends on their location, activity planning
    or outfit planning."""
    try:
        openweathermap_api_key = os.environ.get("openweathermap_api_key")
        owm_url = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?lat={lat}&lon={long}&appid={openweathermap_api_key}"
        )
        owm_response = requests.get(owm_url)
        owm_response_json = owm_response.json()
        return {
            "temp":        owm_response_json["main"]["temp"] - 273.15,
            "description": owm_response_json["weather"][0]["description"],
            "icon":        owm_response_json["weather"][0]["icon"],
        }
    except Exception as e:
        print(f"[georgias_tools] weather failed: {e}")
        return {"error": str(e)}


@mcp.tool()
async def get_address(lat: float, long: float) -> str:
    """Uses lat and long coordinates to reverse geocode an address result.
    Use when context around location is required."""
    result = reverse_geocode(lat, long)
    return result or "unknown"


@mcp.tool()
async def check_location_type(lat: float, long: float) -> str:
    """
    Based on latitude and longitude, determines the type of location.
    Returns one of: university, school, library, transit, restaurant,
    cafe, bar, commercial, hospital, health, gym, residential, park,
    workplace, civic, unknown.

    Use when needing the type of location helps determine user
    behaviour or appropriate Nova mode — university → lecture mode, transit → en-route, residential → home context.
    """
    return get_location_type(lat, long)


if __name__ == "__main__":
    mcp.run(transport="http", host="localhost", port=8000, stateless_http=True)
