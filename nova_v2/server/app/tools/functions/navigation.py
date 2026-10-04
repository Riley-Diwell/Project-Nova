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
    injected by intent_surface.py before dispatch (see _run_local_tool). With
    neither, the tool says it doesn't know where the user is - no default origin
  - mode (optional) - transit | walking | driving, defaults to the user's
    preferred_travel_mode (injected the same way as origin) or DEFAULT_TRAVEL_MODE
  - minutes_until_start (optional) - the calendar commitment's own countdown,
    copied straight from user_state rather than parsed from a clock string.
    When present, the result carries leave_in_minutes (see _leave_in_minutes),
    which is what lets Android schedule a precise alarm for the real leave-by
    moment instead of only answering on request.

API SETUP
Needs google_maps_api_key in .env (same key as get_current_address's
reverse-geocode call). Without it, or when the Directions call fails, the tool
says it can't work out the travel time (see _travel_unknown) rather than
inventing one. There is no travel time anywhere in this file that Directions
didn't measure, on purpose.

DISTANCE OVERRIDES
  - Short enough to walk (see WALKING_OVERRIDE_METERS) re-queries Directions
    with mode=walking regardless of what was actually asked for - waiting for a
    bus or finding parking costs more time than just walking it.
  - Already there (see ARRIVED_METERS) skips duration/leave_in_minutes
    entirely and says so instead - there is nothing left to leave for.

THE ERROR TERM
This is the tool the closed loop was designed around, because it is the one with
a real deadline: a lecture at ten and a twenty-minute drive means there is a last
moment to leave. error() can't measure that moment without a network round trip
it must not make on every event, so it measures the next best thing: how soon a
commitment with a place starts. The Directions call it authorises is what
measures the journey itself.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import os
import re

import requests

from app.tools.core.base import BaseTool

MAPS_API_KEY = os.environ.get("google_maps_api_key")

# There is deliberately no default origin. This used to fall back to a fixed
# point in Canberra city when the phone sent no location, which produced a
# confident, real-looking route ("24 minutes, head southwest on Theatre Lane")
# from somewhere the user might not be - in the same turn get_current_address
# was truthfully saying it had no location. With no origin the tool says so.

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


def _user_local_now(utc_offset_minutes: int) -> datetime:
    """Right now, as the user's own wall clock reads it - a tz-aware UTC
    datetime whose hour/minute fields are the user's local time, not the
    server process's. Building arrival_time off this (rather than a naive
    datetime.now(), which .timestamp() would interpret in the server's own
    system timezone - UTC on Cloud Run) is what lets "9:00am" mean 9am for the
    user asking, not 9am wherever the container happens to run."""
    return datetime.now(timezone.utc) + timedelta(minutes=utc_offset_minutes)


def _user_local_clock_to_utc(local_wall_clock: datetime, utc_offset_minutes: int) -> datetime:
    """Reverses _user_local_now: given a datetime whose wall-clock fields are
    the user's local time (as produced by replacing hour/minute on
    _user_local_now's result), returns the real UTC instant it names."""
    return local_wall_clock - timedelta(minutes=utc_offset_minutes)

# ANU's timetable software spells a class location "<room(s)>_<Building Name>
# Bldg <number>" (e.g. "Lab 1.08 and Lab 1.09_Birch Bldg 35") - it never says
# "ANU" or "university" anywhere in the string. See _clean_campus_location.
_ANU_TIMETABLE_PATTERN = re.compile(
    r'(?:^|_)\s*(?P<building>[^_]*?\b(?:Bldg|Building)\.?\s*\d+\w*)\s*$',
    re.IGNORECASE,
)

# error()'s ramp, in minutes until the next located commitment starts. At or
# beyond CHECK_FROM the term is zero; it rises linearly to 1 at URGENT_BY. The
# trip itself is unmeasured at this point, so these are not slack - they are
# how early a real Directions check is worth spending, which has to be early
# enough that most trips can still be left for on time.
CHECK_FROM_MINUTES = 60
URGENT_BY_MINUTES = 30

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
                    "event_title": {
                        "type": "string",
                        "description": (
                            "That calendar entry's own title, copied verbatim - "
                            "never invented or paraphrased. Same condition as "
                            "minutes_until_start: only when the destination came "
                            "from a calendar entry, left out otherwise. Not used "
                            "for the travel calculation itself; it only lets the "
                            "leave-soon notification name what the trip is for."
                        ),
                    },
                },
                "required": ["destination"],
            },
        )

    def error(self, observation: Any) -> Optional[float]:
        """How soon the next commitment with a place starts, in [0, 1].

            error = 0    when minutes_until_start >= CHECK_FROM_MINUTES
                    1    when minutes_until_start <= URGENT_BY_MINUTES
                    linear between

        What this measures is an unmeasured journey coming up, not slack. Slack
        needs a travel time, and the only honest source of one is the Directions
        API - which this must not call, because the error term runs on every
        event and a network call here would put a Maps round trip in front of
        every one. So it says "somewhere to be soon, go and measure it", and the
        Action the model resolves afterwards makes the real Directions call. If
        that call can't produce a travel time, the tool says so (_travel_unknown)
        and the phone tells the user, rather than anything here guessing one.

        There used to be a table of Canberra travel estimates here, which made
        "when do I leave" work only for places whose name matched a row - in
        practice, uni - and stay silent everywhere else.

        Zero when there is nowhere to be. A commitment without a location is a
        commitment with no journey to be late for, and an empty horizon is the
        case NOVA should be silent in.
        """
        commitment = getattr(observation, "next_commitment", None)
        if commitment is None or not getattr(commitment, "location", None):
            return 0.0

        minutes = commitment.minutes_until_start
        if minutes <= URGENT_BY_MINUTES:
            return 1.0
        if minutes >= CHECK_FROM_MINUTES:
            return 0.0
        return round((CHECK_FROM_MINUTES - minutes) / (CHECK_FROM_MINUTES - URGENT_BY_MINUTES), 4)

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        destination = tool_input.get("destination", "")
        arrival_time = tool_input.get("arrival_time")
        origin = tool_input.get("origin")
        mode = tool_input.get("mode") or DEFAULT_TRAVEL_MODE
        minutes_until_start = tool_input.get("minutes_until_start")
        # Injected by intent_surface.py's _run_local_tool, same as origin/mode -
        # how far the user's wall clock is from UTC, so arrival_time ("9:00am")
        # resolves against their clock rather than the server process's.
        utc_offset_minutes = tool_input.get("utc_offset_minutes") or 0

        if not destination:
            return {
                "success": False,
                "spoken": "Where would you like to go?",
                "needs_clarification": True,
            }

        # The room numbers a timetable glues onto the front are noise to a maps
        # API, and "Birch" alone is ambiguous with every other Birch building
        # anywhere - see _clean_campus_location. A no-op for a destination that
        # was never in that shape to begin with (e.g. "Coffee Club").
        destination = _clean_campus_location(destination)

        if not origin:
            result = _travel_unknown(
                destination,
                f"I don't have your location right now, so I can't work out "
                f"how long it takes to get to {destination} from where you are.",
                "no location from the phone",
            )
            result["needs_location"] = True
            return result

        if not MAPS_API_KEY:
            return _maps_unavailable(destination)
        return _query_google_maps(
            origin, destination, arrival_time, mode, minutes_until_start, utc_offset_minutes
        )


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
                        mode: str, minutes_until_start: Any = None,
                        utc_offset_minutes: int = 0) -> dict:
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
            arr_hour, arr_min = _parse_time(arrival_time)
            local_wall_clock = _user_local_now(utc_offset_minutes).replace(
                hour=arr_hour, minute=arr_min, second=0, microsecond=0
            )
            arr_dt = _user_local_clock_to_utc(local_wall_clock, utc_offset_minutes)
            params["arrival_time"] = int(arr_dt.timestamp())
        except Exception as e:
            print(f"[NavigationTool] couldn't parse arrival_time {arrival_time!r}: {e}")

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

        result = _travel_unknown(
            destination,
            f"I couldn't find a route to '{destination}' — could you confirm the address?",
            "no route found",
        )
        result["needs_clarification"] = True
        result["api_used"] = True
        return result
    except Exception as e:
        print(f"[NavigationTool] Maps API failed: {e}")

    return _maps_unavailable(destination)


def _travel_unknown(destination: str, spoken: str, reason: str) -> dict:
    """The tool's answer when there is no measured travel time to give.

    `travel_unknown` is what intent_surface.py looks for to tell the phone,
    which notifies the user on an ambient check instead of staying silent - a
    "you need to leave" that silently never comes is worse than being told to
    check the route yourself.
    """
    return {
        "success": False,
        "destination": destination,
        "spoken": spoken,
        "reason": reason,
        "travel_unknown": True,
    }


def _maps_unavailable(destination: str) -> dict:
    """No API key, or the Directions call itself failed."""
    result = _travel_unknown(
        destination,
        f"I can't work out how long it takes to get to {destination} right now - "
        f"I can't reach maps.",
        "maps unavailable",
    )
    result["api_used"] = False
    return result


def _clean_campus_location(destination: str) -> str:
    """Strips a timetable location down to the building Directions/Places can
    actually resolve, and anchors it to the campus.

    "Lab 1.08 and Lab 1.09_Birch Bldg 35" fails both APIs as given: the room
    numbers glued on the front are noise to a geocoder, and "Birch" alone is
    ambiguous with every other Birch-named place Google knows about. Keeping
    only the "<Building Name> Bldg <number>" part and naming the university
    explicitly is what lets a real Directions/Places lookup find the right
    one - see _ANU_TIMETABLE_PATTERN.

    A no-op for anything not in that shape (e.g. "Coffee Club", a destination
    named directly), so this is safe to call unconditionally on every
    destination the tool receives.
    """
    match = _ANU_TIMETABLE_PATTERN.search(destination)
    if not match:
        return destination
    return f"{match.group('building').strip()}, Australian National University, Canberra ACT"


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
