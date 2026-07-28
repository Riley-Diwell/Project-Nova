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
from typing import Any, Literal
from uuid import UUID

import requests
from anthropic import Anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

# import nova libraries
from schemas.user_state import UserState
from schemas.event import Event
from gain.gain_store import GainStore
from gain.overrides import GainOverrides
from tools.registry import ToolRegistry
from tools.dispatcher import Dispatcher
from tools.calendar_tool import CalendarTool
from tools.memory_tool import MemoryTool
from tools.navigation import NavigationTool
from tools.notification_management import NotificationManagementTool

import memory

load_dotenv()

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
    "Your text is read aloud by text-to-speech, so write plain spoken "
    "sentences - no markdown, bullets or asterisks. "
    "Never invent facts you were not given. Reading a pattern out of "
    "recent_episodes is not inventing - that is what they are there for. "
    "For ambient/system events (no direct user request), it's fine to stay "
    "quiet if nothing warrants saying aloud. "
    "But if the triggering event is the user directly speaking to you (a "
    "voice event) and you cannot fulfil what they asked - e.g. you lack the "
    "right tool, or you don't have the information - never return an empty "
    "string. Instead say briefly that you're not sure, and say why (for "
    "example: \"I'm not sure - I don't have a way to add calendar events "
    "yet.\"). Check recent_episodes and the memory tool before you fall back "
    "on that: not being told something outright is not the same as having "
    "nothing to go on. "
    "CALENDAR AND TIME. Now is user_state.local_time; every event carries "
    "start_local and end_local, already in the user's timezone. Work only from "
    "those three. Read the date off start_local to say whether something is "
    "today or tomorrow, and the clock time off it to say when - do not assume "
    "the next event in a list is tomorrow, check its date against local_time's "
    "date. Never date-reckon from the triggering event's timestamp or from any "
    "*_millis field: those are UTC instants and land on a different calendar "
    "day from the user's for much of the day. "
    "current_events/upcoming_events is only the next couple of hours, not the "
    "calendar. Whenever the user names a day or a span - today, tomorrow, this "
    "week, a date - call get_calendar_range for that span in local time, even "
    "if upcoming_events already appears to hold something; that list is a "
    "preview and is routinely incomplete for the day being asked about. "
    "recent_episodes holds the last few logged episodes of the same event type "
    "(oldest first), each with the event, the user_state at the time, and the "
    "action taken and how it landed (outcome). This is your memory of THIS "
    "user - it is evidence about who they are, not just a transcript of past "
    "turns. Read it before answering and use it two ways: to stay consistent "
    "with what you did before, and to answer questions about the user "
    "themselves - their habits, routines, preferences, their 'usual'. When "
    "they ask something nobody ever stated outright but their history points "
    "at - a favourite food or place, the route they always take, when they "
    "normally leave, what they always dismiss - answer from that history "
    "instead of saying you don't know, and say what you based it on (e.g. "
    "\"you've asked me to take you there most mornings this week, so I'd say "
    "that one\"). Repetition is the signal: the same destination, app or "
    "choice recurring across episodes is a preference, even when the wording "
    "differs each time. Say you don't know only when the history genuinely "
    "has nothing bearing on the question; an empty list means nothing "
    "comparable has been logged yet. They are history, not the current "
    "situation. "
    "recent_episodes is inferred from behaviour; the memory tool is the "
    "separate notebook of things the user asked you outright to remember. "
    "Save to it whenever they ask you to remember or note something, and "
    "recall from it when they ask what they told you or what they noted. "
    "When both bear on an answer, prefer what they stated over what you "
    "inferred."
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

# The gain store is passed in so register() below loads each tool's saved gain
# rather than starting every tool back at DEFAULT_GAIN - without it the dial in
# the Android app (GainScreen.kt, via /tools/gain) would reset on each restart.
_GAIN_STORE = GainStore()

_REGISTRY = ToolRegistry(gain_store=_GAIN_STORE)
_REGISTRY.register(NavigationTool())
_REGISTRY.register(NotificationManagementTool())
_REGISTRY.register(MemoryTool())
# Runs on the phone, not here (CLIENT_TOOLS below) - registered so it carries a
# gain, and so gets a dial in the app's Gain tab like the others.
_REGISTRY.register(CalendarTool())
_DISPATCHER = Dispatcher(_REGISTRY)

REGISTRY_TOOLS: list[dict[str, Any]] = [
    {"name": s.name, "description": s.description, "input_schema": s.input_schema}
    for s in _REGISTRY.get_schemas()
]

# get_current_address is a context tool - a lookup with no gain, so it stays out
# of the registry (see dispatcher.py) and is declared inline above. Everything
# else Claude can call now comes from the registry, so each one has a dial.
TOOLS: list[dict[str, Any]] = [GET_CURRENT_ADDRESS_TOOL, *REGISTRY_TOOLS]

CLIENT_TOOLS: set[str] = {"get_calendar_range"}

# The registry is built here, so this is where the gain package gets pointed at
# it. main.py's GET/PUT /tools/gain go straight through this - the tuning logic
# itself lives in gain/overrides.py, next to the reinforcement that moves the
# same numbers from the other direction.
GAIN_OVERRIDES = GainOverrides(_REGISTRY, _GAIN_STORE)


def _run_local_tool(name: str, tool_input: dict[str, Any], location_ctx: str | None) -> Any:
    if name == "get_current_address":
        return _reverse_geocode(location_ctx)
    if _REGISTRY.has(name):
        if location_ctx and "origin" not in tool_input:
            tool_input = {**tool_input, "origin": location_ctx}
        return _DISPATCHER.dispatch_reactive(name, tool_input)
    return {"error": f"unknown tool: {name}"}


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
    actions: list[str] = []


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

    main.py logs the current episode before calling run(), so that row is
    dropped here - it is already in the payload as `event`, not history.
    Non-fatal: without Supabase configured this just yields no history.
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

    payload = {
        "event": event.model_dump(mode="json"),
        "user_state": user_state.model_dump(mode="json"),
        "recent_episodes": _recent_episodes(event),
    }

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": json.dumps(payload)},
    ]
    return _run_loop(messages, MAX_ITERATIONS, event.id, user_state.location_ctx)


def resume(session_id: str, tool_result: Any) -> IntentResult | NeedMoreResult:
    """Resumes a conversation paused on a CLIENT_TOOLS call, feeding the
    client-supplied result back in as that tool's result. Raises KeyError if
    session_id is unknown (already resumed, or the process restarted)."""
    pending = _PENDING_SESSIONS.pop(session_id, None)
    if pending is None:
        raise KeyError(f"unknown or expired session_id: {session_id!r}")

    messages: list[dict[str, Any]] = pending["messages"]
    messages.append({
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": pending["tool_use_id"],
            "content": json.dumps(tool_result),
        }],
    })
    return _run_loop(messages, MAX_ITERATIONS, pending["event_id"], pending.get("location_ctx"))


def _run_loop(
    messages: list[dict[str, Any]],
    iterations_left: int,
    event_id: UUID,
    location_ctx: str | None,
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
            return IntentResult(event_id=event_id, speech=speech, actions=[])

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
    return IntentResult(event_id=event_id, speech="", actions=[])