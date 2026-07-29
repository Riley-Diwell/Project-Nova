"""
intent_surface/loop.py - Section 5.3: Intent Surface  (Georgia)

STATUS: wip

WHAT THIS FILE IS
brief description
    1. 

WHO USES THIS
- Georgia: main.py's /event handler will call run(user_state) 
"""

# import necessary libraries
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

import requests
from anthropic import Anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

# import nova libraries
from schemas.user_state import UserState
from schemas.event import Event
from tools.registry import ToolRegistry
from tools.dispatcher import Dispatcher
from tools.navigation import NavigationTool
from tools.notification_management import NotificationManagementTool

import memory

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

MODEL = "claude-haiku-4-5"
MAX_ITERATIONS = 5

# How many past episodes of the same event_type to hand Claude as context.
# memory.read() returns the whole log, so this is a prompt-size cap, not a query
# limit - keep it small.
RECENT_EPISODES = 5

MAPS_API_KEY = os.environ.get("google_maps_api_key")

# Set NOVA_MOCK_LLM=1 in .env to test the /event pipeline (schema validation,
# routing, wire contract) without calling the real Anthropic API - useful for
# local testing without spending API credits.
MOCK_LLM = os.environ.get("NOVA_MOCK_LLM", "").strip().lower() in ("1", "true", "yes")

SYSTEM_PROMPT = (
    "You are NOVA, an ambient assistant. You receive the triggering event "
    "(e.g. what the user said) and their current state, both as JSON. "
    "Decide whether to say anything (short, natural speech - empty string "
    "if nothing warrants saying aloud) and whether to invoke any tools. "
    "Never fabricate context. "
    "For ambient/system events (no direct user request), it's fine to stay "
    "quiet if nothing warrants saying aloud. "
    "But if the triggering event is the user directly speaking to you (a "
    "voice event) and you cannot fulfil what they asked - e.g. you lack the "
    "right tool, or you don't have the information - never return an empty "
    "string. Instead say briefly that you're not sure, and say why (for "
    "example: \"I'm not sure - I don't have a way to check the weather "
    "yet.\"). "
    "user_state.current_events/upcoming_events is only a short-range snapshot "
    "(now plus the next couple of hours) - it is NOT the whole calendar. If "
    "the user asks about a range outside that snapshot (today, tomorrow, "
    "next week, next month, a specific date), call get_calendar_range rather "
    "than guessing or claiming you don't have the information - use the "
    "payload's local_time field as 'now' to compute the range. "
    "The payload's event.timestamp is always UTC - never read it as, or speak "
    "it aloud as, the user's current time/date. local_time is that same "
    "instant already converted using user_state.utc_offset_minutes, i.e. the "
    "user's actual wall-clock time - always use local_time when asked what "
    "time or date it is. "
    "recent_episodes holds the last few logged episodes of the same event type "
    "(oldest first), each with the event and the user_state at the time. Use "
    "them to spot patterns and stay consistent with what you did before - they "
    "are history, not the current situation, and an empty list just means "
    "nothing comparable has been logged yet."
    "Every calendar event (in user_state.current_events/upcoming_events, and "
    "in get_calendar_range results) carries start_millis/end_millis (raw UTC "
    "epoch milliseconds) alongside start_local/end_local (the same instants "
    "already converted to the user's local wall-clock time). Never read or "
    "speak start_millis/end_millis directly - always use start_local/end_local "
    "when telling the user when an event is."
)


# --- local tools -------------------------------------------------------------

GET_CURRENT_ADDRESS_TOOL: dict[str, Any] = {
    "name": "get_current_address",
    "description": (
        "Reverse-geocodes the user's current coordinates (from their "
        "location_ctx in user_state) into a human-readable address. Call "
        "this when the user asks where they are - never speak raw "
        "lat/lng coordinates aloud."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
    },
}

GET_CALENDAR_RANGE_TOOL: dict[str, Any] = {
    "name": "get_calendar_range",
    "description": (
        "Reads the user's calendar live from their device over an arbitrary "
        "date range. Call this whenever a calendar question reaches outside "
        "the current_events/upcoming_events already present in user_state "
        "(e.g. 'today', 'tomorrow', 'next week', 'next month', a specific "
        "date). Use the payload's local_time as 'now' to work out the range "
        "in the user's local calendar (e.g. their 'today' midnight-to-midnight), "
        "then convert both ends back to UTC using user_state.utc_offset_minutes "
        "before calling this tool - from_time/to_time are always UTC. Never "
        "fabricate calendar events you don't have - call this instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "from_time": {
                "type": "string",
                "description": (
                    "ISO 8601 UTC start of the range, with a 'Z' suffix - same format as "
                    "the triggering event's timestamp, e.g. 2026-07-28T00:00:00Z. Convert "
                    "from the user's local date/time using user_state.utc_offset_minutes."
                ),
            },
            "to_time": {
                "type": "string",
                "description": (
                    "ISO 8601 UTC end of the range, with a 'Z' suffix, e.g. 2026-07-29T00:00:00Z. "
                    "Convert from the user's local date/time using user_state.utc_offset_minutes."
                ),
            },
        },
        "required": ["from_time", "to_time"],
    },
}

ADD_CALENDAR_EVENT_TOOL: dict[str, Any] = {
    "name": "add_calendar_event",
    "description": (
        "Adds an event to the user's device calendar (which syncs onward to "
        "their Google account). Call this whenever the user asks to create, "
        "add, book, or schedule a calendar event. Use the payload's "
        "local_time as 'now' to resolve any relative time the user gives "
        "(e.g. 'tomorrow at 3pm'), then convert both start_time and end_time "
        "to UTC using user_state.utc_offset_minutes before calling - both "
        "are always UTC. If the user doesn't give a duration, default to one "
        "hour after start_time."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short title for the event, e.g. 'Dentist appointment'.",
            },
            "start_time": {
                "type": "string",
                "description": (
                    "ISO 8601 UTC start of the event, with a 'Z' suffix, e.g. "
                    "2026-07-29T05:00:00Z. Convert from the user's local date/time "
                    "using user_state.utc_offset_minutes."
                ),
            },
            "end_time": {
                "type": "string",
                "description": (
                    "ISO 8601 UTC end of the event, with a 'Z' suffix. Default to "
                    "one hour after start_time if the user gave no duration."
                ),
            },
            "description": {
                "type": "string",
                "description": "Optional extra detail about the event.",
            },
        },
        "required": ["title", "start_time", "end_time"],
    },
}

_REGISTRY = ToolRegistry()
_REGISTRY.register(NavigationTool())
_REGISTRY.register(NotificationManagementTool())
_DISPATCHER = Dispatcher(_REGISTRY)

REGISTRY_TOOLS: list[dict[str, Any]] = [
    {"name": s.name, "description": s.description, "input_schema": s.input_schema}
    for s in _REGISTRY.get_schemas()
]

TOOLS: list[dict[str, Any]] = [
    GET_CURRENT_ADDRESS_TOOL, GET_CALENDAR_RANGE_TOOL, ADD_CALENDAR_EVENT_TOOL, *REGISTRY_TOOLS,
]

CLIENT_TOOLS: set[str] = {"get_calendar_range"}


def _add_calendar_event(tool_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Validates an add_calendar_event call and builds the action Android's
    CalendarWriter will execute on-device. Returns (tool_result, action) -
    action is None if validation failed, so nothing gets queued."""
    title = tool_input.get("title")
    start_time = tool_input.get("start_time")
    end_time = tool_input.get("end_time")
    if not title or not start_time or not end_time:
        return {
            "success": False,
            "error": "title, start_time, and end_time are all required",
        }, None

    action = {
        "type": "calendar.create_event",
        "title": title,
        "start_time": start_time,
        "end_time": end_time,
        "description": tool_input.get("description"),
    }
    return {"success": True, "queued_for_device": True}, action


def _run_local_tool(name: str, tool_input: dict[str, Any], location_ctx: str | None) -> Any:
    if name == "get_current_address":
        return _reverse_geocode(location_ctx)
    if _REGISTRY.has(name):
        if location_ctx and "origin" not in tool_input:
            tool_input = {**tool_input, "origin": location_ctx}
        return _DISPATCHER.dispatch_reactive(name, tool_input)
    return {"error": f"unknown tool: {name}"}


def _epoch_millis_to_local_iso(epoch_millis: int, utc_offset_minutes: int) -> str:
    """Converts a raw UTC epoch-millis timestamp (as posted in CalendarEventInfo's
    start_millis/end_millis) into the user's local wall-clock time. LLMs can't reliably
    do epoch-millis arithmetic themselves, so we do it here instead of asking them to."""
    dt = datetime.fromtimestamp(epoch_millis / 1000, tz=timezone.utc) + timedelta(minutes=utc_offset_minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _localize_calendar_events(events: list[dict[str, Any]], utc_offset_minutes: int) -> None:
    """Adds start_local/end_local (human wall-clock strings) alongside the raw
    start_millis/end_millis already on each event dict, in place."""
    for ev in events:
        if "start_millis" in ev:
            ev["start_local"] = _epoch_millis_to_local_iso(ev["start_millis"], utc_offset_minutes)
        if "end_millis" in ev:
            ev["end_local"] = _epoch_millis_to_local_iso(ev["end_millis"], utc_offset_minutes)


# --- loop return type -------------------------------------------------------
def _reverse_geocode(location_ctx: str | None) -> dict[str, Any]:
    """Turn a 'lat,lng' location_ctx string into a formatted address via
    the Google Geocoding API. Same MAPS_API_KEY as functions/navigation.py."""
    if not location_ctx:
        return {"success": False, "error": "no location available yet"}

    try:
        lat_str, lng_str = location_ctx.split(",")
        lat, lng = float(lat_str), float(lng_str)
    except (ValueError, AttributeError):
        return {"success": False, "error": f"unparseable location_ctx: {location_ctx!r}"}

    if not MAPS_API_KEY:
        return {
            "success": False,
            "error": "no Maps API key configured",
            "coordinates": f"{lat},{lng}",
        }

    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"latlng": f"{lat},{lng}", "key": MAPS_API_KEY},
            timeout=5,
        )
        data = r.json()
        if data.get("status") == "OK" and data.get("results"):
            return {"success": True, "address": data["results"][0]["formatted_address"]}
        return {
            "success": False,
            "error": f"geocode status: {data.get('status')}",
            "coordinates": f"{lat},{lng}",
        }
    except Exception as e:
        print(f"[loop] reverse geocode failed: {e}")
        return {"success": False, "error": str(e), "coordinates": f"{lat},{lng}"}



class IntentResult(BaseModel):
    status: Literal["final"] = "final"
    event_id: UUID
    speech: str
    actions: list[dict[str, Any]] = []


class NeedMoreResult(BaseModel):
    """
    Returned instead of IntentResult when Claude called a CLIENT_TOOLS tool.
    The paused conversation is held in _PENDING_SESSIONS under session_id;
    the caller (main.py) hands request_type/from_time/to_time to Android,
    which resolves them on-device and posts the result to /event/continue
    to resume the same conversation (see resume()).
    """
    status: Literal["need_more"] = "need_more"
    event_id: UUID
    session_id: str
    request_type: str
    from_time: str
    to_time: str


_PENDING_SESSIONS: dict[str, dict[str, Any]] = {}


# --- the loop ---------------------------------------------------------------

def _recent_episodes(event: Event) -> list[dict[str, Any]]:
    """
    The last RECENT_EPISODES logged episodes of this event's type, oldest first,
    for pattern analysis (schema.sql: "the Intent Surface reads rows back").
    """
    try:
        rows = memory.read("event_type", event.type)
    except Exception as e:
        print(f"[memory] read skipped: {e}")
        return []

    current_id = str(event.id)
    past = [r for r in rows if (r.get("event") or {}).get("id") != current_id]
    print(f"[memory] {len(past)} past {event.type!r} episodes, using last {RECENT_EPISODES}")
    return [
        {
            "created_at": r.get("created_at"),
            "event": r.get("event"),
            "user_state": r.get("user_state"),
            "action": r.get("action"),
            "outcome": r.get("outcome"),
        }
        for r in past[-RECENT_EPISODES:]
    ]


def run(user_state: UserState, event: Event) -> IntentResult | NeedMoreResult:
    if MOCK_LLM:
        text = getattr(event, "text", None)
        return IntentResult(
            event_id=event.id,
            speech=f"[mock] received event type={event.type!r}"
                   + (f" text={text!r}" if text else ""),
            actions=[],
        )

    # event.timestamp is always UTC; local_time is that same instant converted
    # via the user's own offset, so the model never has to do the tz math itself.
    local_time = event.timestamp + timedelta(minutes=user_state.utc_offset_minutes)
    user_state_dump = user_state.model_dump(mode="json")
    _localize_calendar_events(user_state_dump.get("current_events", []), user_state.utc_offset_minutes)
    _localize_calendar_events(user_state_dump.get("upcoming_events", []), user_state.utc_offset_minutes)

    payload = {
        "event": event.model_dump(mode="json"),
        "user_state": user_state_dump,
        "local_time": local_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "recent_episodes": _recent_episodes(event),
    }

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": json.dumps(payload)},
    ]
    return _run_loop(
        messages, MAX_ITERATIONS, event.id, user_state.location_ctx, [],
        user_state.utc_offset_minutes,
    )


def resume(session_id: str, tool_result: Any) -> IntentResult | NeedMoreResult:
    """Resumes a conversation paused on a CLIENT_TOOLS call, feeding the
    client-supplied result back in as that tool's result. Raises KeyError if
    session_id is unknown (already resumed, or the process restarted)."""
    pending = _PENDING_SESSIONS.pop(session_id, None)
    if pending is None:
        raise KeyError(f"unknown or expired session_id: {session_id!r}")

    utc_offset_minutes = pending.get("utc_offset_minutes", 0)
    if isinstance(tool_result, dict) and isinstance(tool_result.get("events"), list):
        _localize_calendar_events(tool_result["events"], utc_offset_minutes)

    messages: list[dict[str, Any]] = pending["messages"]
    messages.append({
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": pending["tool_use_id"],
            "content": json.dumps(tool_result),
        }],
    })
    return _run_loop(
        messages, MAX_ITERATIONS, pending["event_id"], pending.get("location_ctx"),
        pending.get("actions", []), utc_offset_minutes,
    )


def _run_loop(
    messages: list[dict[str, Any]],
    iterations_left: int,
    event_id: UUID,
    location_ctx: str | None,
    actions: list[dict[str, Any]],
    utc_offset_minutes: int,
) -> IntentResult | NeedMoreResult:
    # iterate until an appropriate answer is reached
    for _ in range(iterations_left):
        # call model
        print(f"[loop] tools={[t['name'] for t in TOOLS]!r}")
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        print(f"[loop] stop_reason={response.stop_reason!r}")

        # finished reasoning
        if response.stop_reason == "end_turn":
            speech = "".join(
                b.text for b in response.content if b.type == "text"
            )
            if not speech:
                print(f"[loop] empty speech - raw content: {response.content!r}")
            print(f"[loop] final speech={speech!r}")
            return IntentResult(event_id=event_id, speech=speech, actions=actions)

        # if a tool is called
        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            client_call = next(
                (b for b in response.content if b.type == "tool_use" and b.name in CLIENT_TOOLS),
                None,
            )
            if client_call is not None:
                session_id = str(uuid.uuid4())
                _PENDING_SESSIONS[session_id] = {
                    "messages": messages,
                    "tool_use_id": client_call.id,
                    "event_id": event_id,
                    "location_ctx": location_ctx,
                    "actions": actions,
                    "utc_offset_minutes": utc_offset_minutes,
                }
                return NeedMoreResult(
                    event_id=event_id,
                    session_id=session_id,
                    request_type=client_call.name,
                    from_time=client_call.input.get("from_time", ""),
                    to_time=client_call.input.get("to_time", ""),
                )

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    if block.name == "add_calendar_event":
                        result, action = _add_calendar_event(block.input)
                        if action is not None:
                            actions.append(action)
                    else:
                        result = _run_local_tool(block.name, block.input, location_ctx)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                    })
            messages.append({"role": "user", "content": tool_results})
            continue

        # unexpected stop_reason (max_tokens, refusal, pause_turn, ...)
        print(f"[loop] breaking on unexpected stop_reason={response.stop_reason!r}")
        break

    print("[loop] exited loop with no end_turn - returning empty speech")
    return IntentResult(event_id=event_id, speech="", actions=actions)