"""
 Connects calendar events to proactive navigation

WHAT THIS FILE IS
loop.py receives CalendarTriggerEvents and has access to UserState which
includes upcoming_events. This module is called by loop.py to decide whether
an upcoming calendar event at a different location should trigger a proactive
navigation suggestion.

HOW FITS IN
  CalendarTriggerEvent / upcoming_events in UserState
       
  should_suggest_navigation()
         True
  dispatcher.should_run("navigation_departure_time", trigger="inferred", ...)
         proposed=True
  loop.py asks user: "You have a lecture at ANU at 9am — want to know when to leave?"
       
  navigation_departure_time tool fires with destination + arrival_time

proactive navigation path the seed data plants — the model can also infer it reactively from a voice query, but
this module handles the background/automatic trigger.

USAGE (in loop.py, after receiving any event with upcoming_events)
  from calendar_nav_bridge import get_nav_proposal

  proposal = get_nav_proposal(user_state, current_location_type)
  if proposal:
      # proposal = {"destination": "ANU", "arrival_time": "9:00am",
      #             "minutes_until_event": 45, "event_title": "Lecture"}
      # pass to dispatcher.should_run("navigation_departure_time", "inferred", confidence)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from app.schemas.user_state import UserState, CalendarEventInfo
    from app.maps_client import get_distance_matrix
except ImportError:
    from schemas.user_state import UserState, CalendarEventInfo
    from maps_client import get_distance_matrix

#auggest navigation if event is within this many minutes
SUGGEST_WINDOW_MINUTES = 90

# Don't suggest if event is sooner than this — too late to be useful
TOO_LATE_MINUTES = 5

# Only suggest if the event has a location that looks non-trivial
_TRIVIAL_LOCATIONS = {"", "online", "zoom", "teams", "meet", "remote", "virtual"}


def get_nav_proposal(
    user_state: UserState,
    current_location: str | None = None,
) -> dict[str, Any] | None:
    """
    Returns a nav proposal dict if there's an upcoming event worth
    suggesting navigation for, else None.

    Call this from loop.py after any event that updates user_state.
    The caller then passes the result to dispatcher.should_run().

    Returns:
      {
        "destination":          str,   # event location
        "arrival_time":         str,   # "HH:MM"
        "minutes_until_event":  int,
        "event_title":          str,
        "origin":               str,   # user_state.location_ctx
      }
    or None.
    """
    upcoming = user_state.upcoming_events
    if not upcoming:
        return None

    now_ms = _now_ms()
    origin = current_location or user_state.location_ctx or ""

    for event in upcoming:
        minutes_until = (event.start_millis - now_ms) / 60_000

        # Outside the suggestion window
        if minutes_until > SUGGEST_WINDOW_MINUTES:
            continue
        if minutes_until < TOO_LATE_MINUTES:
            continue

        # No useful location
        location = (event.location or "").strip()
        if location.lower() in _TRIVIAL_LOCATIONS:
            continue

        # Don't suggest if the user is already there (same location string)
        if origin and _same_location(origin, location):
            continue

        arrival_time = _millis_to_local_time(event.start_millis)

        return {
            "destination":         location,
            "arrival_time":        arrival_time,
            "minutes_until_event": int(minutes_until),
            "event_title":         event.title,
            "origin":              origin,
        }

    return None


def check_enough_time(
    origin: str,
    destination: str,
    arrival_time_ms: int,
    mode: str = "transit",
    buffer_minutes: int = 5,
) -> dict[str, Any]:
    """
    Check whether the user has enough time to get somewhere.
    Uses Distance Matrix for a fast single-pair check.

    Returns:
      {
        "enough_time": bool,
        "travel_mins": int,
        "margin_mins": int,   # positive = comfortable, negative = late
        "spoken":      str,
      }
    """
    result = get_distance_matrix(
        origins=[origin],
        destinations=[destination],
        mode=mode,
    )

    if not result.get("success"):
        return {"enough_time": None, "spoken": "Could not check travel time."}

    try:
        travel_sec  = result["rows"][0]["results"][0]["duration_sec"]
        travel_mins = travel_sec // 60
    except (KeyError, IndexError):
        return {"enough_time": None, "spoken": "Could not check travel time."}

    now_ms      = _now_ms()
    avail_mins  = (arrival_time_ms - now_ms) / 60_000
    margin      = int(avail_mins - travel_mins - buffer_minutes)
    enough      = margin >= 0

    if enough:
        spoken = (
            f"You have about {int(avail_mins)} minutes until then and it takes "
            f"{travel_mins} minutes to get there — you should be fine."
        )
    else:
        spoken = (
            f"It takes {travel_mins} minutes to get there but you only have "
            f"{int(avail_mins)} minutes — you may be late."
        )

    return {
        "enough_time": enough,
        "travel_mins": travel_mins,
        "margin_mins": margin,
        "spoken":      spoken,
    }


#Helpers 

def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _millis_to_local_time(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000)
    return dt.strftime("%-H:%M%p").lower()   # e.g. "9:00am"


def _same_location(a: str, b: str) -> bool:
    """Very rough check — avoids suggesting nav when already at the destination."""
    a_lower = a.lower().strip()
    b_lower = b.lower().strip()
    # If either is a lat/lng string, don't try to compare as text
    if "," in a_lower and any(c.isdigit() for c in a_lower):
        return False
    return a_lower in b_lower or b_lower in a_lower
