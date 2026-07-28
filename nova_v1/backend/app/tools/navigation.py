"""
navigation_departure_time - Function tool: when does the user need to leave
to get somewhere on time.

PHRASES THAT SHOULD MAP HERE
  "when do I leave", "how long to get to", "what time should I head off",
  "when should I leave for", "how do I get to", "am I going to make it"

INPUT
  - destination (required) - where they want to go
  - arrival_time (optional) - when they need to be there
  - origin (optional) - defaults to the user's current location_ctx,
    injected by intent_surface/loop.py before dispatch (see _run_local_tool)
  - mode (optional) - transit | walking | driving, defaults to transit

API SETUP
Needs google_maps_api_key in .env (same key as get_current_address's
reverse-geocode call). If it isn't set, falls back to hardcoded
Canberra travel time estimates so this is still demo-able without a key.
"""

from datetime import datetime, timedelta
from typing import Any
import os
import re

import requests

from .base import BaseTool

MAPS_API_KEY = os.environ.get("google_maps_api_key")

# Last-resort origin when neither an explicit origin nor the user's
# location_ctx is available (e.g. direct/offline calls to this tool).
DEFAULT_HOME = {"lat": -35.2809, "lng": 149.1300}

# Rough Canberra-specific fallback estimates, used when MAPS_API_KEY is unset
# or the Directions API call fails.
_FALLBACK_ESTIMATES = {
    "anu": 20, "university": 20,
    "city": 15, "civic": 15, "canberra centre": 15,
    "airport": 30, "queanbeyan": 25,
}


class NavigationTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="navigation_departure_time",
            description=(
                "Calculates when the user needs to leave to reach a destination "
                "on time using public transport, walking, or driving. Call when "
                "the user asks when to leave, how long it takes to get "
                "somewhere, or whether they need to leave now."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "destination": {
                        "type": "string",
                        "description": (
                            "Where the user wants to go, e.g. 'ANU', "
                            "'Canberra Centre', 'home'. Can be a place name "
                            "or address."
                        ),
                    },
                    "arrival_time": {
                        "type": "string",
                        "description": (
                            "When the user needs to arrive, e.g. '9:00am', "
                            "'14:30'. If not provided, calculates travel time "
                            "from now."
                        ),
                    },
                    "origin": {
                        "type": "string",
                        "description": (
                            "Where the user is leaving from. Defaults to the "
                            "user's current location if not provided."
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["transit", "walking", "driving"],
                        "description": "Travel mode. Defaults to transit.",
                    },
                },
                "required": ["destination"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        destination = tool_input.get("destination", "")
        arrival_time = tool_input.get("arrival_time")
        origin = tool_input.get("origin") or f"{DEFAULT_HOME['lat']},{DEFAULT_HOME['lng']}"
        mode = tool_input.get("mode", "transit")

        if not destination:
            return {
                "success": False,
                "spoken": "Where would you like to go?",
                "needs_clarification": True,
            }

        if MAPS_API_KEY:
            return _query_google_maps(origin, destination, arrival_time, mode)
        return _estimate_without_api(destination, arrival_time)


def _query_google_maps(origin: str, destination: str,
                        arrival_time: str | None, mode: str) -> dict:
    """Call Google Maps Directions API and return departure time result."""
    params = {
        "origin": origin,
        "destination": destination,
        "mode": mode,
        "key": MAPS_API_KEY,
    }

    if arrival_time:
        try:
            now = datetime.now()
            arr_hour, arr_min = _parse_time(arrival_time)
            arr_dt = now.replace(hour=arr_hour, minute=arr_min, second=0)
            params["arrival_time"] = int(arr_dt.timestamp())
        except Exception:
            pass

    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/directions/json",
            params=params, timeout=5,
        )
        data = r.json()
        if data.get("status") == "OK":
            leg = data["routes"][0]["legs"][0]
            duration = leg["duration"]["text"]
            depart = leg.get("departure_time", {}).get("text", "now")
            arrive = leg.get("arrival_time", {}).get("text", "unknown")
            steps = leg.get("steps", [])
            first_step = steps[0].get("html_instructions", "").replace("<b>", "").replace("</b>", "") if steps else ""

            spoken = f"Leave {depart} — takes {duration} to {destination}"
            if arrival_time:
                spoken += f", arriving at {arrive}"
            if first_step:
                spoken += f". First: {first_step[:60]}"

            return {
                "success": True,
                "duration": duration,
                "depart_at": depart,
                "arrive_at": arrive,
                "destination": destination,
                "mode": mode,
                "spoken": spoken,
                "api_used": True,
            }
    except Exception as e:
        print(f"[NavigationTool] Maps API failed: {e}")

    return _estimate_without_api(destination, arrival_time)


def _estimate_without_api(destination: str, arrival_time: str | None) -> dict:
    """Rough fallback when the Maps API isn't available or the call failed."""
    dest_lower = destination.lower()
    travel_mins = next(
        (v for k, v in _FALLBACK_ESTIMATES.items() if k in dest_lower), 25
    )

    spoken = f"It usually takes about {travel_mins} minutes to get to {destination}"
    if arrival_time:
        try:
            now = datetime.now()
            h, m = _parse_time(arrival_time)
            arr = now.replace(hour=h, minute=m)
            depart = arr - timedelta(minutes=travel_mins + 5)
            spoken = (
                f"Leave by {depart.strftime('%H:%M')} to reach "
                f"{destination} by {arrival_time} — about {travel_mins} minutes away"
            )
        except Exception:
            pass

    return {
        "success": True,
        "duration": f"{travel_mins} minutes (estimate)",
        "destination": destination,
        "spoken": spoken,
        "api_used": False,
    }


def _parse_time(time_str: str) -> tuple[int, int]:
    m = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', time_str.lower())
    if not m:
        raise ValueError(f"Cannot parse time: {time_str}")
    hour = int(m.group(1))
    mins = int(m.group(2)) if m.group(2) else 0
    ampm = m.group(3)
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return hour, mins
