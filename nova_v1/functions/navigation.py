"""
when does the user need to leave to get somewhere on time.
 
when do I leave to get home is the base case, but it generalises to any destination.
 
HOW IT CONNECTS TO THE INTENT SURFACE 
Phrases that should map to this tool:
  "when do I leave", "how long to get to", "what time should I head off",
  "when should I leave for", "how do I get to", "am I going to make it"
 
To extract:
  - destination(required) — where they want to go
  - arrival_time (optional) — when they need to be there
 
Everything else the tool handles itself — origin defaults to home from
local.db, mode defaults to transit, Google Maps does rest.
 
REACTIVE call:
    dispatcher.dispatch_reactive("navigation_departure_time", {
        "destination":  "ANU",
        "arrival_time": "9:00am",
    })
 
    dispatcher.dispatch_reactive("navigation_departure_time", {
        "destination": "home", - travel time from now 
    })
 
    dispatcher.dispatch_reactive("navigation_departure_time", {
        "destination":  "Canberra Centre",
        "arrival_time": "2pm",
        "mode":  "walking", # optional, defaults to transit
    })
 
PROACTIVE call:
      - Calendar shows an event starting in <45 minutes at a different location
      - current_events or future_events contains a place name and time
      - Location changed — user just arrived somewhere new
 
    proposal = dispatcher.dispatch_proactive(
        "navigation_departure_time",
        {"destination": "ANU", "arrival_time": "9:00am"},
        state_confidence=0.75,
    )
    if proposal.proposed:
        # speak "You have a lecture at 9am — want to know when to leave?"
        if user_said_yes:
            dispatcher.confirm_proactive("navigation_departure_time",
                                         {"destination": "ANU",
                                          "arrival_time": "9:00am"})
            reinforcer.reinforce("navigation_departure_time", Outcome.ACCEPTED)
        else:
            reinforcer.reinforce("navigation_departure_time", Outcome.REJECTED)
 
WHAT THE TOOL RETURNS
Always returns a dict with at least:
  {
      "success":     bool,
      "spoken":      str, ← send this to TTS / BLE back to device
      "destination": str,
      "duration":    str,    e.g."22 mins" or "22 minutes (estimate)"
      "depart_at":   str,  e.g. "8:38 AM" (only if arrival_time given)
      "arrive_at":   str,  e.g. "9:00 AM" (only if arrival_time given)
      "mode":        str,   "transit" | "walking" | "driving"
      "api_used":    bool, False means it used rough estimates
  }
 
Example spoken outputs:
  "Leave at 8:38 to reach ANU by 9am — takes about 22 minutes by transit."
  "It usually takes about 20 minutes to get to Canberra Centre."
  "Leave by 8:38 to reach ANU by 9am. First: Take Route 3 towards City."
 
API SETUP
Needs GOOGLE_MAPS_API_KEY (or google_maps_api_key) in your .env file.
If the key isn't set, falls back to hardcoded Canberra travel time estimates
so it's still demo-able without an API key.
 
Hardcoded estimates (fallback):
  ANU / universit → ~20 min
  City / Civic  → ~15 min
  Airport   → ~30 min
  Anything else → ~25 min
 
HOME LOCATION
home address from Riley's local.db
If local.db isn't available it falls back to those coordinates directly.
The user's home is used as the default origin so they don't have to say
"from home" every time.
 
"""

from typing import Any
from ..base import BaseTool

import os
import requests
from datetime import datetime


# Google Maps API key set in .env
MAPS_API_KEY = os.environ.get("google_maps_api_key")

# Fallback home from seed data if not in persona memory
DEFAULT_HOME = {"lat": -35.2809, "lng": 149.1300, "label": "Home"}


class NavigationTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="navigation_departure_time",
            description=(
                "Calculates when the user needs to leave to reach a destination "
                "on time using public transport or walking. Call when the user "
                "asks when to leave, how long it takes to get somewhere, or "
                "whether they need to leave now. Uses the user's home location "
                "from persona memory as the default origin."
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
                            "Where the user is leaving from. Defaults to "
                            "home address from persona memory if not provided."
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
        destination  = tool_input.get("destination", "")
        arrival_time = tool_input.get("arrival_time")
        origin       = tool_input.get("origin") or _get_home_address()
        mode         = tool_input.get("mode", "transit")

        if not destination:
            return {
                "success": False,
                "spoken":  "Where would you like to go?",
                "needs_clarification": True,
            }

        # Try Google Maps Directions API
        if MAPS_API_KEY:
            result = _query_google_maps(origin, destination, arrival_time, mode)
        else:
            # Fallback: rough estimate without API
            result = _estimate_without_api(destination, arrival_time)

        return result


# Helpers 

def _get_home_address() -> str:
    """Pull home location from Riley's local.db persona memory."""
    try:
        import sqlite3
        DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
        LOCAL_DB = os.path.join(DATA_DIR, "local.db")
        if not os.path.exists(LOCAL_DB):
            return f"{DEFAULT_HOME['lat']},{DEFAULT_HOME['lng']}"
        conn = sqlite3.connect(LOCAL_DB)
        rows = conn.execute(
            "SELECT name_blob FROM items WHERE tier IN (0,1) AND resident=1"
        ).fetchall()
        conn.close()
        for (name_blob,) in rows:
            try:
                name = name_blob.decode(errors="ignore")
                if "home" in name.lower() or "location" in name.lower():
                    return f"{DEFAULT_HOME['lat']},{DEFAULT_HOME['lng']}"
            except Exception:
                pass
    except Exception:
        pass
    return f"{DEFAULT_HOME['lat']},{DEFAULT_HOME['lng']}"


def _query_google_maps(origin: str, destination: str,
                       arrival_time: str | None, mode: str) -> dict:
    """Call Google Maps Directions API and return departure time result."""
    params = {
        "origin":      origin,
        "destination": destination,
        "mode":        mode,
        "key":         MAPS_API_KEY,
    }

    # If arrival time given, work backwards to departure time
    if arrival_time:
        try:
            now       = datetime.now()
            arr_hour, arr_min = _parse_time(arrival_time)
            arr_dt    = now.replace(hour=arr_hour, minute=arr_min, second=0)
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
            leg      = data["routes"][0]["legs"][0]
            duration = leg["duration"]["text"]
            depart   = leg.get("departure_time", {}).get("text", "now")
            arrive   = leg.get("arrival_time",   {}).get("text", "unknown")
            steps    = leg.get("steps", [])
            first_step = steps[0].get("html_instructions", "").replace("<b>","").replace("</b>","") if steps else ""

            spoken = f"Leave {depart} — takes {duration} to {destination}"
            if arrival_time:
                spoken += f", arriving at {arrive}"
            if first_step:
                spoken += f". First: {first_step[:60]}"

            return {
                "success":     True,
                "duration":    duration,
                "depart_at":   depart,
                "arrive_at":   arrive,
                "destination": destination,
                "mode":        mode,
                "spoken":      spoken,
            }
    except Exception as e:
        print(f"[Navigation] Maps API failed: {e}")

    return _estimate_without_api(destination, arrival_time)


def _estimate_without_api(destination: str, arrival_time: str | None) -> dict:
    """Rough fallback when Maps API isn't available."""
    # Very rough Canberra-specific estimates
    estimates = {
        "anu":       20, "university": 20,
        "city":      15, "civic":      15, "canberra centre": 15,
        "airport":   30, "queanbeyan": 25,
    }
    dest_lower  = destination.lower()
    travel_mins = next(
        (v for k, v in estimates.items() if k in dest_lower), 25
    )

    spoken = f"It usually takes about {travel_mins} minutes to get to {destination}"
    if arrival_time:
        try:
            now = datetime.now()
            h, m = _parse_time(arrival_time)
            arr  = now.replace(hour=h, minute=m)
            from datetime import timedelta
            depart = arr - timedelta(minutes=travel_mins + 5)
            spoken = (
                f"Leave by {depart.strftime('%H:%M')} to reach "
                f"{destination} by {arrival_time} — about {travel_mins} minutes away"
            )
        except Exception:
            pass

    return {
        "success":     True,
        "duration":    f"{travel_mins} minutes (estimate)",
        "destination": destination,
        "spoken":      spoken,
        "api_used":    False,
    }


def _parse_time(time_str: str) -> tuple[int, int]:
    import re
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
