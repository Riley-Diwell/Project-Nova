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
  - mode (optional) - transit | walking | driving, defaults to the user's
    preferred_travel_mode (injected the same way as origin) or DEFAULT_TRAVEL_MODE
  - minutes_until_start (optional) - the calendar commitment's own countdown,
    copied straight from user_state rather than parsed from a clock string.
    When present, the result carries leave_in_minutes (see _leave_in_minutes),
    which is what lets Android schedule a precise alarm for the real leave-by
    moment instead of only answering on request.

API SETUP
Needs google_maps_api_key in .env (same key as get_current_address's
reverse-geocode call). Without it, the tool answers from a small table of
hardcoded Canberra estimates so the demo still works - and for anywhere not in
that table it says it does not know, rather than inventing a duration. There is no
default travel time anywhere in this file, on purpose: see _FALLBACK_ESTIMATES.

DISTANCE OVERRIDES (real Directions results only - the offline estimate table
has no coordinates to measure either of these against)
  - Short enough to walk (see WALKING_OVERRIDE_METERS) re-queries Directions
    with mode=walking regardless of what was actually asked for - waiting for a
    bus or finding parking costs more time than just walking it.
  - Already there (see ARRIVED_METERS) skips duration/leave_in_minutes
    entirely and says so instead - there is nothing left to leave for.

THE ERROR TERM
This is the tool the closed loop was designed around, because it is the one with
a real deadline: a lecture at ten and a twenty-minute drive means there is a last
moment to leave, and how close the user is to that moment is a number. See
error() - it deliberately uses the offline estimate table rather than the
Directions API, because deciding whether to speak must not cost a network round
trip on every event.
"""

from datetime import datetime, timedelta
from typing import Any, Optional
import os
import re

import requests

from app.tools.core.base import BaseTool

MAPS_API_KEY = os.environ.get("google_maps_api_key")

# Last-resort origin when neither an explicit origin nor the user's
# location_ctx is available (e.g. direct/offline calls to this tool).
DEFAULT_HOME = {"lat": -35.2809, "lng": 149.1300}

# Silent fallback when neither the model nor the user's preferred_travel_mode
# supplies one. A uni student is more likely to walk, bus or bike than drive,
# so "driving" - the old default - was the wrong thing to assume about someone
# who may not own a car.
DEFAULT_TRAVEL_MODE = "transit"

# Resolved-place cache, keyed by the destination string as given (lowercased,
# stripped). A recurring weekly class re-asks the same "when do I leave for
# X" question every week; without this, every ask spends a Places lookup on a
# location that never moves. Process-lifetime only, no eviction - see
# Phase 2 planning notes on revisiting this if ambient pings raise volume.
_RESOLVED_PLACE_CACHE: dict[str, tuple[str, str]] = {}


def _cache_key(destination: str) -> str:
    return destination.strip().lower()

# Rough Canberra-specific fallback estimates, used when MAPS_API_KEY is unset
# or the Directions API call fails.
_FALLBACK_ESTIMATES = {
    "anu": 20, "university": 20,
    "city": 15, "civic": 15, "canberra centre": 15,
    "airport": 30, "queanbeyan": 25,
}

# How much slack has to be left before leaving stops being a live concern. Above
# this the error term is zero: half an hour of spare time is not a problem NOVA
# should be volunteering an opinion about, however proactive its dial is.
SLACK_HORIZON_MINUTES = 30

# Below this, walking beats waiting for a bus or finding parking, whatever mode
# was actually asked for - roughly a 15-minute walk at an average pace. Applied
# to the route Directions actually returned, not a straight-line guess, so a
# destination that's close as the crow flies but cut off by a river or a fence
# doesn't get walking directions that don't exist.
WALKING_OVERRIDE_METERS = 1200

# Below this the user isn't travelling to the destination, they're standing in
# it - see _directions_result. A "leave in N minutes" alert for somewhere
# already reached is exactly the kind of ambient noise the gain mechanism
# exists to avoid, so this is checked before any duration/leave_in_minutes math
# runs at all.
ARRIVED_METERS = 150


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
                        "description": (
                            "Travel mode. Defaults to the user's declared "
                            "preferred_travel_mode, or transit if they haven't "
                            "set one."
                        ),
                    },
                    "minutes_until_start": {
                        "type": "number",
                        "description": (
                            "Minutes from now until the commitment starts, copied "
                            "directly from its minutes_until_start in "
                            "current_events/upcoming_events - only when the "
                            "destination came from a calendar entry. This is what "
                            "lets Nova schedule a precise alarm for the real "
                            "leave-by moment instead of only answering when asked. "
                            "Leave it out for a destination with no calendar "
                            "anchor, e.g. 'get me downtown by 3pm'."
                        ),
                    },
                },
                "required": ["destination"],
            },
        )

    def error(self, observation: Any) -> Optional[float]:
        """How little slack is left before the user has to leave, in [0, 1].

        The measurement: the next thing the user is committed to has a place and
        a start time, getting there takes a while, so there is a latest departure
        that still meets it. Slack is the gap between now and that departure.

            slack = minutes_until_start - travel_minutes
            error = 0                       when slack >= SLACK_HORIZON_MINUTES
                    1 - slack / horizon     as slack closes
                    1                       when slack <= 0 (already too late)

        Which gives the property the whole overhaul is for: the term is zero when
        nothing is diverging, rises in proportion as the deadline approaches, and
        saturates once the user is already late - past that point there is no
        worse news to deliver, so a larger number would only distort the gain.

        Zero when there is nowhere to be. A commitment without a location is a
        commitment with no journey to be late for, and an empty horizon is the
        case NOVA should be silent in.

        Travel time comes from this module's offline estimate table, never from
        the Directions API. The error term runs on every event, including ambient
        ones, so a network call here would put a Maps round trip in front of every
        notification. The estimate is coarse; it only has to be good enough to
        rank "comfortable" against "leave now", and the Action the model resolves
        afterwards is what gets the real routing.

        **None when the destination has no estimate**, which declares navigation
        open-loop for this turn. There is no default travel time, so slack is not
        computable, so there is no error - and the two ways of covering that up are
        both worse:

          returning 0.0    claims the journey was measured and found comfortable.
                           It would read in the log as "nothing was diverging" when
                           the truth is "nobody knows", and those need different
                           fixes.
          assuming 25 min  invents the divergence. A place two hours away would
                           look fine until twenty-five minutes before, and NOVA
                           would then act with total confidence on a number with
                           nothing behind it.

        Saying nothing is the honest option, and its cost is visible: NOVA will not
        volunteer a departure time for a destination it has never measured. The fix
        is a real travel estimate available off the request path, not a constant.
        """
        commitment = getattr(observation, "next_commitment", None)
        if commitment is None or not getattr(commitment, "location", None):
            return 0.0

        travel = _estimated_travel_minutes(commitment.location)
        if travel is None:
            return None

        slack = commitment.minutes_until_start - travel

        if slack <= 0:
            return 1.0
        if slack >= SLACK_HORIZON_MINUTES:
            return 0.0
        return round(1.0 - slack / SLACK_HORIZON_MINUTES, 4)

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        destination = tool_input.get("destination", "")
        arrival_time = tool_input.get("arrival_time")
        origin = tool_input.get("origin") or f"{DEFAULT_HOME['lat']},{DEFAULT_HOME['lng']}"
        mode = tool_input.get("mode") or DEFAULT_TRAVEL_MODE
        minutes_until_start = tool_input.get("minutes_until_start")

        if not destination:
            return {
                "success": False,
                "spoken": "Where would you like to go?",
                "needs_clarification": True,
            }

        if MAPS_API_KEY:
            return _query_google_maps(origin, destination, arrival_time, mode, minutes_until_start)
        return _estimate_without_api(destination, arrival_time, minutes_until_start)


def _leave_in_minutes(minutes_until_start: Any, travel_minutes: float) -> float | None:
    """Minutes from now until the user must leave, or None if there is no
    calendar-sourced minutes_until_start to measure against.

    Deliberately relative, not a clock time: minutes_until_start already comes
    from Android's own clock (CalendarSignal.kt), so subtracting a travel
    estimate from it needs no server-side "what time is it for this user"
    reasoning at all - the same reason error() works in minutes rather than
    parsing arrival_time against datetime.now(). A free-form ask with no
    calendar anchor ('get me downtown by 3pm') has nothing to subtract from,
    so this stays None rather than guessing one from the parsed clock string.

    Negative means the departure moment has already passed.
    """
    if minutes_until_start is None:
        return None
    try:
        minutes_until_start = float(minutes_until_start)
    except (TypeError, ValueError):
        return None
    return round(minutes_until_start - travel_minutes, 1)


def _prefer_walking_if_close(origin: str, destination: str, data: dict, mode: str) -> tuple[dict, str]:
    """Re-queries Directions with mode=walking when the route actually asked for
    turns out to be short enough that walking beats it regardless of what the
    model or the user's preferred_travel_mode chose - see WALKING_OVERRIDE_METERS.
    A no-op when mode is already "walking": there's nothing shorter to prefer it
    over. `destination` must be the same param _query_google_maps already resolved
    (a place_id: reference or the raw string), not the original user-facing label.
    """
    if mode == "walking":
        return data, mode
    leg = data["routes"][0]["legs"][0]
    if leg["distance"]["value"] > WALKING_OVERRIDE_METERS:
        return data, mode
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/directions/json",
            params={"origin": origin, "destination": destination, "mode": "walking", "key": MAPS_API_KEY},
            timeout=5,
        )
        walking_data = r.json()
        if walking_data.get("status") == "OK":
            return walking_data, "walking"
        print(f"[NavigationTool] walking-override retry status={walking_data.get('status')!r}")
    except Exception as e:
        print(f"[NavigationTool] walking-override query failed: {e}")
    return data, mode


def _query_google_maps(origin: str, destination: str, arrival_time: str | None,
                        mode: str, minutes_until_start: Any = None) -> dict:
    """Call Google Maps Directions API and return departure time result."""
    cached = _RESOLVED_PLACE_CACHE.get(_cache_key(destination))
    directions_destination = f"place_id:{cached[0]}" if cached else destination

    params = {
        "origin": origin,
        "destination": directions_destination,
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
        status = data.get("status")
        if status == "OK":
            data, mode = _prefer_walking_if_close(origin, directions_destination, data, mode)
            # A cache hit already asked the user's forgiveness for guessing once
            # ("Assuming you meant...") the first time this destination came up -
            # a recurring weekly class shouldn't repeat that caveat every ask.
            label = cached[1] if cached else destination
            return _directions_result(data, label, mode, arrival_time, minutes_until_start)

        # Directions couldn't resolve the destination as given (e.g. ZERO_RESULTS,
        # NOT_FOUND) - this is exactly the shape a misheard voice transcript takes
        # ("77 Scandalbrooke Crescent" for "77 Scantlebury Crescent"), so before
        # giving up, ask Places' Find Place From Text for its best fuzzy match on
        # the same text and retry Directions against that resolved place instead.
        print(f"[NavigationTool] Directions API status={status!r} "
              f"error_message={data.get('error_message')!r} destination={destination!r}")

        match = _find_place_from_text(destination)
        if match is not None:
            place_id, matched_address = match
            params["destination"] = f"place_id:{place_id}"
            r2 = requests.get(
                "https://maps.googleapis.com/maps/api/directions/json",
                params=params, timeout=5,
            )
            data2 = r2.json()
            if data2.get("status") == "OK":
                print(f"[NavigationTool] resolved {destination!r} -> {matched_address!r} via Places")
                _RESOLVED_PLACE_CACHE[_cache_key(destination)] = (place_id, matched_address)
                data2, mode = _prefer_walking_if_close(origin, params["destination"], data2, mode)
                result = _directions_result(data2, matched_address, mode, arrival_time, minutes_until_start)
                result["resolved_from"] = destination
                result["spoken"] = f"(Assuming you meant {matched_address}) " + result["spoken"]
                return result
            print(f"[NavigationTool] Directions retry against Places match "
                  f"{matched_address!r} status={data2.get('status')!r}")

        return {
            "success": False,
            "spoken": f"I couldn't find a route to '{destination}' — could you confirm the address?",
            "needs_clarification": True,
            "api_used": True,
        }
    except Exception as e:
        print(f"[NavigationTool] Maps API failed: {e}")

    return _estimate_without_api(destination, arrival_time, minutes_until_start)


def _estimated_travel_minutes(destination: str) -> int | None:
    """How long getting to `destination` probably takes, without asking anyone.

    Substring match against the estimate table, longest key first so "canberra
    centre" is not beaten to it by "city". Coarse by construction: this is the
    number error() ranks urgency with, and the real routing happens later in the
    Action the model resolves.

    **None when the destination matches nothing.** There is deliberately no
    default - see the comment on _FALLBACK_ESTIMATES. Callers have to decide what
    to do with not knowing, and both of them say so rather than covering it up.
    """
    where = destination.lower()
    for key in sorted(_FALLBACK_ESTIMATES, key=len, reverse=True):
        if key in where:
            return _FALLBACK_ESTIMATES[key]
    return None


def _directions_result(data: dict, destination: str, mode: str, arrival_time: str | None,
                        minutes_until_start: Any = None) -> dict:
    """Builds the success dict from a Directions API OK response."""
    leg = data["routes"][0]["legs"][0]

    if leg["distance"]["value"] <= ARRIVED_METERS:
        # Already there. Deliberately no leave_in_minutes - there is nothing left
        # to leave for, so Android's DepartureAlarmScheduler gets nothing to set
        # an alarm against, and the system prompt's ambient case (only speak if
        # leaving is imminent) reads this as "not imminent" and stays quiet.
        return {
            "success": True,
            "duration": "0 minutes",
            "destination": destination,
            "mode": mode,
            "spoken": f"You're already at {destination}.",
            "api_used": True,
            "arrived": True,
        }

    duration = leg["duration"]["text"]
    duration_minutes = leg["duration"]["value"] / 60
    depart = leg.get("departure_time", {}).get("text")
    arrive = leg.get("arrival_time", {}).get("text")
    steps = leg.get("steps", [])
    first_step = steps[0].get("html_instructions", "").replace("<b>", "").replace("</b>", "") if steps else ""

    if depart:
        spoken = f"Leave {depart} — takes {duration} to {destination}"
        if arrival_time and arrive:
            spoken += f", arriving at {arrive}"
    else:
        spoken = f"Takes {duration} to {destination}"

    if first_step:
        spoken += f". First: {first_step[:60]}"

    result = {
        "success": True,
        "duration": duration,
        "depart_at": depart,
        "arrive_at": arrive,
        "destination": destination,
        "mode": mode,
        "spoken": spoken,
        "api_used": True,
    }
    leave_in = _leave_in_minutes(minutes_until_start, duration_minutes)
    if leave_in is not None:
        result["leave_in_minutes"] = leave_in
    return result


def _find_place_from_text(text: str) -> tuple[str, str] | None:
    """
    Best-effort fuzzy resolve of a raw (possibly misheard) destination string via
    Places' Find Place From Text, which tolerates typos/mishearings that the
    Directions API's stricter geocoding rejects outright. Returns
    (place_id, formatted_address) or None if Places couldn't find a candidate
    either, or the request itself failed.
    """
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
            params={
                "input": text,
                "inputtype": "textquery",
                "fields": "place_id,formatted_address",
                "key": MAPS_API_KEY,
            },
            timeout=5,
        )
        data = r.json()
        if data.get("status") == "OK" and data.get("candidates"):
            candidate = data["candidates"][0]
            return candidate["place_id"], candidate["formatted_address"]
        print(f"[NavigationTool] Find Place From Text status={data.get('status')!r} "
              f"for {text!r}")
    except Exception as e:
        print(f"[NavigationTool] Find Place From Text failed: {e}")
    return None


def _estimate_without_api(destination: str, arrival_time: str | None,
                           minutes_until_start: Any = None) -> dict:
    """Rough fallback when the Maps API isn't available or the call failed.

    Only answers for destinations there is actually an estimate for. For anything
    else it says it does not know, because the alternative is a spoken sentence
    that sounds exactly as confident as a real answer and is not one.
    """
    travel_mins = _estimated_travel_minutes(destination)

    if travel_mins is None:
        return {
            "success": False,
            "destination": destination,
            "spoken": (
                f"I don't know how long it takes to get to {destination} - I can't "
                f"reach maps at the moment."
            ),
            "needs_clarification": True,
            "reason": "no travel estimate available offline",
            "api_used": False,
        }

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

    result = {
        "success": True,
        "duration": f"{travel_mins} minutes (estimate)",
        "destination": destination,
        "spoken": spoken,
        "api_used": False,
    }
    leave_in = _leave_in_minutes(minutes_until_start, travel_mins)
    if leave_in is not None:
        result["leave_in_minutes"] = leave_in
    return result


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
