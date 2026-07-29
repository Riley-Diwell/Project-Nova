"""
 Shared Google Maps client 

Wraps four Google Maps APIs used across the codebase:
  1. Directions  - routing + public transport (navigation.py)
  2. Distance Matrix - travel time between multiple points
  3. Places - location type classification (georgias_tools.py)
  4. Geocoding  - address <-> lat/lng (already in georgias_tools.py)

All four use the same key: google_maps_api_key in .env
The googlemaps Python SDK handles retries, rate limiting and key injection.

USAGE
from maps_client import get_directions, get_distance_matrix, get_location_type

# Routing
result = get_directions(
    origin="home",
    destination="ANU",
    arrival_time="9:00am",
    mode="transit",
)
# result["spoken"]    -> "Leave at 8:38 — takes 22 mins by bus"
# result["steps"]     -> list of transit steps with line numbers

# Distance matrix (multiple origins or destinations at once)
result = get_distance_matrix(
    origins=["-35.2809,149.1300"],
    destinations=["ANU", "Canberra Centre", "Airport"],
)

# Location type
loc_type = get_location_type(-35.2777, 149.1185)
# -> "university"
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Any

import googlemaps
from dotenv import load_dotenv

load_dotenv()

_API_KEY = os.environ.get("google_maps_api_key")

#avoids raising at import time when key is absent
_client: googlemaps.Client | None = None


def _gmaps() -> googlemaps.Client | None:
    global _client
    if _client is None and _API_KEY:
        _client = googlemaps.Client(key=_API_KEY)
    return _client


# 1. Directions (routing + public transport) 

def get_directions(
    origin: str,
    destination: str,
    arrival_time: str | None = None,
    departure_time: str | None = None,
    mode: str = "transit",
) -> dict[str, Any]:
    """
    Get route from origin to destination.

    Returns a dict with:
      success       bool
      spoken        str   — ready for TTS
      duration      str   — e.g. "22 mins"
      depart_at     str   — e.g. "8:38 AM"
      arrive_at     str   — e.g. "9:00 AM"
      steps         list  — transit steps (line name, stop names, walk legs)
      mode          str
      api_used      bool

    Falls back to hardcoded Canberra estimates when API key is absent or call fails.
    """
    if not _gmaps():
        return _fallback(destination, arrival_time)

    kwargs: dict[str, Any] = {
        "origin":      origin,
        "destination": destination,
        "mode":        mode,
    }

    # Prefer arrival_time for "get me there by X" queries —
    # the API works backwards from it to give a departure time.
    if arrival_time:
        try:
            kwargs["arrival_time"] = _parse_to_epoch(arrival_time)
        except ValueError:
            pass
    elif departure_time:
        try:
            kwargs["departure_time"] = _parse_to_epoch(departure_time)
        except ValueError:
            pass
    else:
        # Default: transit from now
        kwargs["departure_time"] = "now"

    try:
        results = _gmaps().directions(**kwargs)
        if not results:
            return _fallback(destination, arrival_time)

        route = results[0]
        leg   = route["legs"][0]

        duration  = leg["duration"]["text"]
        depart_at = leg.get("departure_time", {}).get("text", "now")
        arrive_at = leg.get("arrival_time",   {}).get("text", "unknown")

        steps     = _parse_steps(leg.get("steps", []), mode)
        first_leg = steps[0]["instruction"] if steps else ""

        if arrival_time:
            spoken = (
                f"Leave at {depart_at} to reach {destination} by {arrival_time} "
                f"— {duration} by {mode}"
            )
        else:
            spoken = f"Takes about {duration} to {destination} by {mode}"
            if depart_at and depart_at != "now":
                spoken += f". Next departure at {depart_at}"

        if first_leg:
            spoken += f". First: {first_leg}"

        return {
            "success":     True,
            "spoken":      spoken,
            "duration":    duration,
            "depart_at":   depart_at,
            "arrive_at":   arrive_at,
            "destination": destination,
            "mode":        mode,
            "steps":       steps,
            "api_used":    True,
        }

    except Exception as e:
        print(f"[maps_client] directions failed: {e}")
        return _fallback(destination, arrival_time)


def _parse_steps(raw_steps: list, mode: str) -> list[dict]:
    """
    Parse leg steps into a clean list.
    For transit steps, includes line name, headsign and departure stop.
    For walking steps, includes distance and instruction.
    """
    steps = []
    for s in raw_steps:
        step_mode = s.get("travel_mode", mode).lower()

        if step_mode == "transit":
            td = s.get("transit_details", {})
            line = td.get("line", {})
            steps.append({
                "mode":        "transit",
                "line":        line.get("short_name") or line.get("name", ""),
                "headsign":    td.get("headsign", ""),
                "depart_stop": td.get("departure_stop", {}).get("name", ""),
                "arrive_stop": td.get("arrival_stop",  {}).get("name", ""),
                "duration":    s.get("duration", {}).get("text", ""),
                "instruction": (
                    f"Take {line.get('short_name') or line.get('name', 'bus')} "
                    f"towards {td.get('headsign', '')} "
                    f"from {td.get('departure_stop', {}).get('name', '')}"
                ),
            })
        else:
            steps.append({
                "mode":        "walking",
                "distance":    s.get("distance", {}).get("text", ""),
                "duration":    s.get("duration", {}).get("text", ""),
                "instruction": _strip_html(
                    s.get("html_instructions", s.get("maneuver", "Walk"))
                ),
            })

    return steps


# 2. Distance Matrix 

def get_distance_matrix(
    origins: list[str],
    destinations: list[str],
    mode: str = "transit",
    departure_time: str = "now",
) -> dict[str, Any]:
    """
    Travel time + distance between multiple origins and destinations.

    Useful for:
      - Comparing how far several upcoming calendar events are
      - Checking whether the user has time to go somewhere and return
        before their next event

    Returns:
      {
        "success": True,
        "rows": [
          {
            "origin": "...",
            "results": [
              {"destination": "...", "duration": "22 mins", "distance": "14.3 km"},
              ...
            ]
          }
        ]
      }
    """
    if not _gmaps():
        return {"success": False, "error": "Maps API key not configured."}

    kwargs: dict[str, Any] = {
        "origins":         origins,
        "destinations":    destinations,
        "mode":            mode,
    }
    if departure_time == "now":
        kwargs["departure_time"] = "now"
    else:
        try:
            kwargs["departure_time"] = _parse_to_epoch(departure_time)
        except ValueError:
            kwargs["departure_time"] = "now"

    try:
        result = _gmaps().distance_matrix(**kwargs)
        rows = []
        for i, row in enumerate(result.get("rows", [])):
            origin_label = origins[i] if i < len(origins) else f"origin_{i}"
            row_results = []
            for j, elem in enumerate(row.get("elements", [])):
                dest_label = destinations[j] if j < len(destinations) else f"dest_{j}"
                if elem.get("status") == "OK":
                    row_results.append({
                        "destination": dest_label,
                        "duration":    elem["duration"]["text"],
                        "distance":    elem["distance"]["text"],
                        "duration_sec": elem["duration"]["value"],
                    })
                else:
                    row_results.append({
                        "destination": dest_label,
                        "error":       elem.get("status", "UNKNOWN"),
                    })
            rows.append({"origin": origin_label, "results": row_results})

        return {"success": True, "mode": mode, "rows": rows}

    except Exception as e:
        print(f"[maps_client] distance matrix failed: {e}")
        return {"success": False, "error": str(e)}


#  3. Location type (Places API) 

# Maps Google Places `types` to a human-readable category Nova uses
_PLACE_TYPE_MAP = {
    # Education
    "university":    "university",
    "school":        "school",
    "library":       "library",

    # Transit
    "transit_station":    "transit",
    "bus_station":        "transit",
    "train_station":      "transit",
    "subway_station":     "transit",
    "light_rail_station": "transit",

    # Eating
    "restaurant":  "restaurant",
    "cafe":        "cafe",
    "bakery":      "cafe",
    "bar":         "bar",
    "food":        "food",

    # Retail / commercial
    "shopping_mall":   "commercial",
    "store":           "commercial",
    "supermarket":     "commercial",
    "convenience_store": "commercial",

    # Health
    "hospital":   "hospital",
    "pharmacy":   "health",
    "gym":        "gym",
    "health":     "health",

    # Residential
    "premise":         "residential",
    "subpremise":      "residential",
    "neighborhood":    "residential",

    # Outdoor / recreation
    "park":      "park",
    "campground": "outdoor",

    # Work / office
    "office":    "workplace",

    # Government / civic
    "local_government_office": "civic",
    "courthouse":  "civic",
    "police":  "civic",
}

def get_location_type(lat: float, lng: float) -> str:
    """
    Returns the type of place at (lat, lng).

    Uses the Google Places "find_place" or reverse geocode types to classify
    the location into one of:
      university, school, library, transit, restaurant, cafe, bar,
      commercial, hospital, health, gym, residential, park, workplace,
      civic, unknown

    Used by check_location_type MCP tool and the FSM to infer
    context (e.g. at a university → lecture mode candidate).
    """
    if not _gmaps():
        return "unknown"

    try:
        # Nearby search with a tight radius to find the most prominent place
        result = _gmaps().places_nearby(
            location=(lat, lng),
            rank_by="distance",
            type=None,        # all types, then we classify
        )
        candidates = result.get("results", [])

        if not candidates:
            return _type_from_geocode(lat, lng)

        # Score each candidate — pick the most specific type we recognise
        for place in candidates[:3]:
            for ptype in place.get("types", []):
                if ptype in _PLACE_TYPE_MAP:
                    print(f"[maps_client] location type: {ptype} → {_PLACE_TYPE_MAP[ptype]}")
                    return _PLACE_TYPE_MAP[ptype]

        # None matched — fall through to geocode types
        return _type_from_geocode(lat, lng)

    except Exception as e:
        print(f"[maps_client] location type failed: {e}")
        return "unknown"


def _type_from_geocode(lat: float, lng: float) -> str:
    """Fallback: infer type from reverse geocode address component types."""
    try:
        results = _gmaps().reverse_geocode((lat, lng))
        for component in results[0].get("address_components", []):
            for t in component.get("types", []):
                if t in _PLACE_TYPE_MAP:
                    return _PLACE_TYPE_MAP[t]
    except Exception:
        pass
    return "unknown"


#4. Geocoding helpers 

def geocode(address: str) -> dict[str, float] | None:
    """
    Convert an address string to {lat, lng}.
    Returns None if the API is unavailable or the address can't be found.
    """
    if not _gmaps():
        return None
    try:
        results = _gmaps().geocode(address)
        if results:
            loc = results[0]["geometry"]["location"]
            return {"lat": loc["lat"], "lng": loc["lng"]}
    except Exception as e:
        print(f"[maps_client] geocode failed: {e}")
    return None


def reverse_geocode(lat: float, lng: float) -> str:
    """
    Convert lat/lng to a human-readable address string.
    Returns empty string on failure.
    """
    if not _gmaps():
        return ""
    try:
        results = _gmaps().reverse_geocode((lat, lng))
        if results:
            return results[0].get("formatted_address", "")
    except Exception as e:
        print(f"[maps_client] reverse_geocode failed: {e}")
    return ""


# Helpers 

def _parse_to_epoch(time_str: str) -> int:
    """Parse a time string like '9:00am' or '14:30' to a Unix timestamp."""
    m = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', time_str.lower())
    if not m:
        raise ValueError(f"Cannot parse time: {time_str!r}")
    hour = int(m.group(1))
    mins = int(m.group(2)) if m.group(2) else 0
    ampm = m.group(3)
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    dt = datetime.now().replace(hour=hour, minute=mins, second=0, microsecond=0)
    # If the time has already passed today, assume tomorrow
    if dt < datetime.now():
        dt += timedelta(days=1)
    return int(dt.timestamp())


def _strip_html(text: str) -> str:
    """Strip HTML tags from Google Maps instruction strings."""
    return re.sub(r"<[^>]+>", "", text).strip()


# Hardcoded Canberra fallbacks when api unpresent 
_CANBERRA_ESTIMATES = {
    "anu": 20, "university": 20, "hanna neumann": 20,
    "city": 15, "civic": 15, "canberra centre": 15,
    "airport": 30, "queanbeyan": 25,
    "bagel": 18, "fyshwick": 18,
    "belconnen": 20, "tuggeranong": 30, "woden": 20,
}


def _fallback(destination: str, arrival_time: str | None) -> dict[str, Any]:
    """Return rough estimate when the Maps API is unavailable."""
    dest_lower   = destination.lower()
    travel_mins  = next(
        (v for k, v in _CANBERRA_ESTIMATES.items() if k in dest_lower), 25
    )

    spoken = f"It usually takes about {travel_mins} minutes to get to {destination}"
    if arrival_time:
        try:
            now   = datetime.now()
            epoch = _parse_to_epoch(arrival_time)
            arr   = datetime.fromtimestamp(epoch)
            depart = arr - timedelta(minutes=travel_mins + 5)
            spoken = (
                f"Leave by {depart.strftime('%H:%M')} to reach {destination} "
                f"by {arrival_time} — about {travel_mins} minutes away (estimate)"
            )
        except Exception:
            pass

    return {
        "success":     True,
        "spoken":      spoken,
        "duration":    f"{travel_mins} minutes (estimate)",
        "destination": destination,
        "steps":       [],
        "api_used":    False,
    }
