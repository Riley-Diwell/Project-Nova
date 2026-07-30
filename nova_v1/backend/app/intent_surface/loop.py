"""
intent_surface/loop.py - Section 5.3: Intent Surface  (Georgia)

STATUS: working draft

WHAT THIS FILE IS
Uses AI to infer intent. 
    1. Runs an Anthropic client. It receives an Event and the User State the phone
computed for it, and produces the words NOVA says plus the Actions it took.
    2. Runs a tool calling loop to process this data and infer intent. 

WHO USES THIS
- main.py's /event and /event/continue handlers call run() and resume().
"""

# import necessary libraries
import json
import os
import re
import uuid
from dataclasses import dataclass, field
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
from control.commands import Command, classify
from control.controller import Decision, ProportionalController, Reason, Turn
from control.observer import Observation, observe, trends_from_facts
from gain.gain_store import GainStore
from gain.overrides import GainOverrides
from gain.reinforcement import Outcome, Reinforcer
from tools.action import Action
from tools.catalogue import CLIENT_TOOLS, build_registry
from tools.dispatcher import Dispatcher

import memory
import persona

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

MODEL = "claude-haiku-4-5"
MAX_ITERATIONS = 5

# How many past Episodes of the same type to hand Claude as short-term context.
# A prompt-size cap as much as a query limit - keep it small.
RECENT_EPISODES = 5

# How many long-term Persona facts to retrieve per event, and how close they
# have to be. Kept small: this is background about the user, competing with
# recent_episodes for the model's attention.
#
# The floor is a noise-drop, NOT a relevance test, and it is deliberately low.
# Measured against the real store (bge-large, question vs third-person
# statement), the two bands overlap and cannot be separated by a threshold:
#
#   relevant   "where do I always go in the mornings" -> 0.499
#              "what is my usual"                     -> 0.442
#   unrelated  "remind me to call mum"                -> 0.492
#              "what is the capital of France"        -> 0.308
#
# So anything high enough to exclude "call mum" also excludes real hits. Top-k
# ranking plus the model's own judgement does the filtering; each fact is
# handed over with its similarity so weak ones can be discounted. Raising this
# past ~0.45 silently empties the persona payload - that is what it did at 0.5.
PERSONA_HITS = 3
PERSONA_MIN_SIMILARITY = 0.35

MAPS_API_KEY = os.environ.get("google_maps_api_key")

# Set NOVA_MOCK_LLM=1 in .env to test the /event pipeline (schema validation,
# routing, wire contract) without calling the real Anthropic API - useful for
# local testing without spending API credits.
MOCK_LLM = os.environ.get("NOVA_MOCK_LLM", "").strip().lower() in ("1", "true", "yes")

# The prompt describes speech and the sources NOVA speaks from. Nothing in it
# describes when to act, because the model no longer decides that: it is offered
# only the tools the Controller authorised, so an unauthorised call is
# structurally impossible rather than prose-discouraged.
SYSTEM_PROMPT = (
    "You are NOVA, an ambient assistant. You receive the triggering event "
    "(e.g. what the user said) and their current state, both as JSON. "
    "Decide what to say - short, natural speech, or an empty string if nothing "
    "warrants saying aloud - and which of the tools you have been given to "
    "call. "
    "Your text is read aloud by text-to-speech, so write plain spoken "
    "sentences - no markdown, bullets or asterisks. It holds only words a "
    "person would say out loud: never your reasoning, and never any mention of "
    "tools, settings or why you did or did not do something. If the right "
    "response is to act quietly, act and leave the text empty. "
    "Never invent facts you were not given. If a tool call you made comes back "
    "unavailable, do not claim to the user that you did the thing anyway - "
    "for example, never say \"I'll remember that\" unless the memory tool "
    "actually ran. Acknowledge what they said without promising an action "
    "that did not happen. "
    "For ambient events - a notification arriving, a calendar trigger - staying "
    "quiet is usually right. But if the user is speaking to you directly and "
    "you cannot do what they asked, never return an empty string: say briefly "
    "that you're not sure and why (for example: \"I'm not sure - I don't have a "
    "way to add calendar events yet.\"). Check the memory tool and the sources "
    "below first; not having been told something outright is not the same as "
    "having nothing to go on. "
    "Not everything the user says is a request. A plain statement, an "
    "observation, something said in passing - these are not requests you have "
    "failed to fulfil, so do not answer them by asking what they wanted. "
    "WEB SEARCH. You have a web_search tool - use it instead of saying you "
    "can't look something up. Call it for anything you don't know from "
    "recent_episodes or user_state and that isn't a settled fact you'd "
    "already know: current events, prices, hours, or finding a place - "
    "restaurants, shops, businesses near the user all count. web_search only "
    "sees the words in your query, not the user's coordinates, so for a "
    "'nearest'/'near me' style question call get_current_address first (if "
    "you don't already have an address for this turn) and put that address "
    "or the city/area it names into the query yourself, e.g. \"kebab "
    "restaurants near 44 Example St, Springfield\". Only fall back to saying "
    "you're not sure after a search comes back with nothing useful - not "
    "before you've tried it. "
    "TIME AND THE CALENDAR. Now is the top-level local_time. Every calendar "
    "entry carries start_local and end_local. All of these are already the "
    "user's own wall clock, so quote them as they are and never convert "
    "anything. Read the date off start_local to say whether something is today "
    "or tomorrow - do not assume the next entry in a list is tomorrow, check "
    "its date against local_time's. "
    "current_events/upcoming_events covers only the next couple of hours, not "
    "the calendar. Whenever the user names a day or a span - today, tomorrow, "
    "this week, a date - call get_calendar_range for it in local time, even if "
    "upcoming_events appears to hold something already: that list is a preview "
    "and is routinely incomplete for the day being asked about. "
    "THREE SOURCES, DIFFERENT LIFETIMES. recent_episodes is the last few logged "
    "episodes of this event type, oldest first - what happened recently, "
    "including the situational detail that stops mattering on its own ('parked "
    "on level 3'). Use it to stay consistent with what you just did. When the "
    "user says 'that', 'it' or otherwise refers to something without naming "
    "it again, check the most recent entry in recent_episodes for what they "
    "said before claiming you have no context - the words are there even on "
    "turns where nothing was saved to memory or persona for them. "
    "The memory tool is the notebook of what the user has told you: save when "
    "they ask you to remember or note something, and recall when they ask what "
    "they told you. "
    "persona is the long-term one: durable facts about who this user is, "
    "retrieved by meaning for this event. These are how you answer about "
    "habits, usuals and preferences - established background rather than "
    "anything just said, true in general rather than necessarily true right "
    "now. The ones marked source 'derived' NOVA worked out by counting repeated "
    "behaviour and carry how many times it was seen; where a derived fact and "
    "something the user actually stated disagree, the stated one wins."
)


# --- local tools -------------------------------------------------------------

# Server-side tool - Anthropic runs the search and hands back results in the
# same response, so there is no local dispatch and no tool_result to send
# back for it (see the tool_use loop below, which only handles CLIENT_TOOLS
# and registry Function tools). claude-haiku-4-5 predates the dynamic-
# filtering tool generation, so this is the basic variant, not _20260209.
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
}

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

# Which tools exist, and which of them resolve on the phone, live in
# tools/catalogue.py - so the Controller and its tests can ask what NOVA can do
# without importing an Anthropic client or reading an API key.
_GAIN_STORE = GainStore()
_REGISTRY = build_registry(_GAIN_STORE)
_DISPATCHER = Dispatcher(_REGISTRY)

def _tool_definition(name: str) -> dict[str, Any]:
    """
    One registered Function tool as the Anthropic API wants it.

    Just the tool: its name, what it does, and what parameters it takes. No
    `trigger` field, because whether the user asked is settled before this runs.
    No gain number and no proactivity guidance in the description either - the
    model is not weighing up whether to act, so telling it the dial position
    would only invite it to.
    """
    schema = _REGISTRY.get_schema(name)


    return {
        "name": schema.name,
        "description": schema.description,
        "input_schema": schema.input_schema,
    }

def _build_tools(authorised: list[str]) -> list[dict[str, Any]]:
    """
    The tool list for one turn: exactly what the Controller authorised, plus the
    context tools.

    A Function tool with no authority this
    turn is simply absent, so calling it is structurally impossible rather than
    prose-discouraged - there is no refusal for the model to argue with,
    relabel, or accidentally explain to the user.

    get_current_address stays out of the Controller's hands: it is a context tool,
    a lookup with no gain, and gating a reverse-geocode would be gating NOVA's
    ability to answer "where am I?" (see dispatcher.py).
    """
    return [
        WEB_SEARCH_TOOL,
        GET_CURRENT_ADDRESS_TOOL,
        *(_tool_definition(name) for name in authorised),
    ]

# The registry is built here, so this is where the gain package gets pointed at
# it. main.py's GET/PUT /tools/gain go straight through this - the tuning logic
# itself lives in gain/overrides.py, next to the reinforcement that moves the
# same numbers from the other direction.
GAIN_OVERRIDES = GainOverrides(_REGISTRY, _GAIN_STORE)
_REINFORCER = Reinforcer(_REGISTRY, _GAIN_STORE)
_CONTROLLER = ProportionalController(_REGISTRY)


def reinforce_episode(episode_id: str, outcome: str) -> dict[str, float]:
    """
    Move the gain of everything an Episode actually did, given the user's
    verdict on it. Returns {tool: new learned value} for what moved.

    Reads the Episode back rather than taking a list of tools, because the
    verdict arrives on a later request than the turn it judges - by the time the
    user has heard NOVA out, the TurnContext is long gone. The Episode is the
    only durable record of what was done, which is a large part of why Actions
    are written to it.

    Every Action with ran=true is scored, reactive ones included. That is a
    deliberate departure from reinforcement.py's "only proactive actions should
    be reinforced": at DEFAULT_GAIN no tool can clear the firing threshold, so
    if only proactive calls counted, nothing would ever be scored and no gain
    would ever move. A tool earns the right to act unasked by being useful when
    asked. See docs/adr/0002.
    """
    try:
        verdict = Outcome(outcome)
    except ValueError:
        print(f"[gain] ignoring unknown outcome {outcome!r}")
        return {}

    try:
        episode = memory.get(episode_id)
    except Exception as e:
        print(f"[gain] reinforcement skipped, episode unreadable: {e}")
        return {}
    if episode is None:
        print(f"[gain] reinforcement skipped, no such episode {episode_id!r}")
        return {}

    moved: dict[str, float] = {}
    for action in Action.from_episode(episode.get("action")):
        if not action.ran or action.tool in moved:
            continue
        try:
            moved[action.tool] = _REINFORCER.reinforce(action.tool, verdict)
        except KeyError:
            # Not a registered Function tool - nothing to tune.
            continue
    return moved


@dataclass
class TurnContext:
    """
    What the loop and the tools need to know about the turn in progress, kept in
    one object so it can be threaded through and parked in _PENDING_SESSIONS
    across a client-tool hop without growing an argument list every time
    something new is needed.
    """

    turn: Turn

    location_ctx: str | None = None

    # Minutes east of UTC for this turn's user_state, kept so a client-tool hop
    # can localise what the phone sends back on the far side of the pause.
    utc_offset_minutes: int = 0

    # Every Function-tool call this turn, in order, with the parameters the model
    # resolved and the control trace behind the decision. ONE structure serves
    # three readers (CONTEXT.md "Action"): it goes out as EventOut.actions for the
    # phone to execute and display, it is written to the episode's `action` column
    # for consolidation to count, and the trace makes that column a replayable
    # control log.

    actions: list[Action] = field(default_factory=list)

    # The episodic_memory row main.py opened for this turn, carried so the row
    # can be closed once the turn resolves - which may be on the far side of a
    # client-tool hop, in /event/continue rather than /event.
    episode_id: str | None = None

    @property
    def ran(self) -> list[str]:
        """Names of the tools that actually ran - logging only."""
        return [a.tool for a in self.actions if a.ran]

    def for_wire(self) -> list[dict[str, Any]]:
        """The Actions as the phone and the Episode see them."""
        return [a.for_wire() for a in self.actions]


def _gate(name: str, ctx: TurnContext) -> dict[str, Any] | None:
    """
    Check one tool call against this turn's control decisions.

    Returns None to allow it, or the tool_result content to hand back instead of
    running it. Context tools (no gain) always pass.

    The Controller decided before the model ran, and
    an unauthorised tool was never offered - so reaching a refusal here means
    either the model called something it was not given, or the same tool was
    refused earlier in this turn. Both are worth catching rather than trusting the
    API not to do.
    """
    if not _REGISTRY.has(name):
        return None  # context tool - no gain, nothing to gate

    decision = ctx.turn.allow(name)
    print(f"[control] {name} {decision.reason.value} "
          f"authority={decision.authority} gain={decision.gain} "
          f"error={decision.error} -> {'run' if decision.authorised else 'REFUSED'}")

    return None if decision.authorised else _refused_result(name)


def _record_action(
    name: str, tool_input: dict[str, Any], ctx: TurnContext, ran: bool
) -> None:
    """
    Record one Action, with the control trace behind it.

    Context tools are skipped: a reverse-geocode lookup says nothing about what
    the user habitually does, and counting it as behaviour would put noise into
    the trends consolidation derives.

    Refused calls are recorded too (ran=False), and they go over the wire as
    well. What NOVA wanted to do and was not authorised to is evidence about the
    user's settings rather than about the user - and the phone needs to be able to
    tell "nothing happened" from "nothing was attempted".

    `trigger` is read off the Controller's decision, not off the tool input. It is
    the same word it always was on the wire, but it now records what actually
    happened - a command ran it, or measured divergence did - rather than what the
    model said about its own call.
    """
    if not _REGISTRY.has(name):
        return
    decision: Decision = ctx.turn.decision(name)
    ctx.actions.append(Action(
        tool=name,
        input=dict(tool_input),
        trigger="requested" if decision.reason is Reason.COMMANDED else "inferred",
        ran=ran,
        **decision.trace(),
    ))


def _refused_result(name: str) -> dict[str, Any]:
    """
    What the model gets back if it calls a tool it was not offered.

    Deliberately terse and deliberately says nothing about gain, thresholds or
    the user's settings.
    """
    return {
        "success": False,
        "available": False,
        "message": (
            f"{name} is not available this turn. Answer with what you have, "
            f"leave out whatever you were going to add, and do not mention this "
            f"to the user."
        ),
    }


def _run_local_tool(name: str, tool_input: dict[str, Any], ctx: TurnContext) -> Any:
    if name == "get_current_address":
        return _reverse_geocode(ctx.location_ctx)
    if _REGISTRY.has(name):
        if ctx.location_ctx and "origin" not in tool_input:
            tool_input = {**tool_input, "origin": ctx.location_ctx}
        # Authorisation already happened in _gate. The dispatcher just runs it.
        result = _DISPATCHER.dispatch_reactive(name, tool_input)
        # Recorded after the call, so a tool that raises is not reported as run -
        # and with the augmented tool_input, so the Action carries the origin the
        # tool actually used rather than the one the model supplied.
        _record_action(name, tool_input, ctx, ran=True)
        return result
    return {"error": f"unknown tool: {name}"}


def _epoch_millis_to_local_iso(epoch_millis: int, utc_offset_minutes: int) -> str:
    """Converts a raw UTC epoch-millis timestamp (as posted in CalendarEventInfo's
    start_millis/end_millis) into the user's local wall-clock time. LLMs can't reliably
    do epoch-millis arithmetic themselves, so we do it here instead of asking them to."""
    dt = datetime.fromtimestamp(epoch_millis / 1000, tz=timezone.utc) + timedelta(minutes=utc_offset_minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")

_UTC_ONLY_FIELDS = ("start_millis", "end_millis", "timestamp")

def _localize_calendar_events(events: list[dict[str, Any]], utc_offset_minutes: int) -> None:
    """Replace each entry's raw epoch millis with local wall-clock strings, in place."""
    for ev in events:
        if "start_millis" in ev:
            ev["start_local"] = _epoch_millis_to_local_iso(ev["start_millis"], utc_offset_minutes)
        if "end_millis" in ev:
            ev["end_local"] = _epoch_millis_to_local_iso(ev["end_millis"], utc_offset_minutes)
        _strip_utc_fields(ev)


def _strip_utc_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove the UTC-only fields from one dict, in place, and return it.

    Applied to the event, the user state and every calendar entry. The model is
    left with local_time, start_local and end_local, which are the only three
    readings of the clock it needs and the only three it can get right.
    """
    for field_name in _UTC_ONLY_FIELDS:
        payload.pop(field_name, None)
    return payload


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

    # Every Action this turn took, in order (CONTEXT.md "Action"). The phone
    # executes the ones it recognises; the same list is written to the episode.
    actions: list[dict[str, Any]] = []
    # Set only for a voice turn that left a question dangling (see
    # _classify_confirmation) - tells Android whether to offer Yes/No
    # buttons alongside the usual text/voice input. None otherwise.
    confirmation: Literal["yes_no", "open"] | None = None

    # The Episode main.py opened for this turn. It closes that row with the
    # Actions above, and passes the id on to the phone so it can name the same
    # Episode when it reports the Outcome.
    episode_id: str | None = None


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

# Carries the *actual* message thread (not a recap of it) across one voice
# turn when the previous turn ended by asking the user a question. Without
# this, the only trace of "what did I ask" a later turn gets is its own
# spoken outcome text sitting in recent_episodes - which is why a bare "yes"
# could take two tries to land: the model had to reparse its own prior
# sentence instead of just seeing the question as a live turn in context.
# Single global slot because this is a single-user ambient device with one
# voice conversation in flight at a time - same assumption _PENDING_SESSIONS
# already makes.
_PENDING_CONFIRMATION: dict[str, Any] | None = None

# How long a dangling question stays answerable. Long enough for a real
# "yes" a few seconds later, short enough that a stale question from minutes
# ago doesn't hijack an unrelated new one.
_PENDING_CONFIRMATION_TTL = timedelta(minutes=3)

# Defensive cap on how many turns a confirmation thread may chain before it's
# abandoned and started fresh - guards against an unbroken run of clarifying
# questions growing the prompt without bound.
_PENDING_CONFIRMATION_MAX_MESSAGES = 12


def _stash_pending_confirmation(messages: list[dict[str, Any]]) -> None:
    """Called when a voice turn ends on a question - keeps the live thread
    so the next voice turn can continue it instead of starting over."""
    global _PENDING_CONFIRMATION
    if len(messages) > _PENDING_CONFIRMATION_MAX_MESSAGES:
        _PENDING_CONFIRMATION = None
        return
    _PENDING_CONFIRMATION = {
        "messages": messages,
        "expires_at": datetime.now(timezone.utc) + _PENDING_CONFIRMATION_TTL,
    }


def _pop_pending_confirmation() -> list[dict[str, Any]] | None:
    """Consumes the pending thread, if any and still fresh. Popped rather
    than peeked so a resolved or abandoned turn can't be answered twice."""
    global _PENDING_CONFIRMATION
    pending, _PENDING_CONFIRMATION = _PENDING_CONFIRMATION, None
    if pending is None:
        return None
    if datetime.now(timezone.utc) > pending["expires_at"]:
        return None
    return pending["messages"]


def _clear_pending_confirmation() -> None:
    """Called when a voice turn resolves without leaving a question open -
    any earlier dangling question is now moot."""
    global _PENDING_CONFIRMATION
    _PENDING_CONFIRMATION = None


_YES_NO_LEAD_IN = re.compile(
    r"^(are|is|am|was|were|do|does|did|can|could|will|would|shall|should|"
    r"have|has|had|may|might|must)\b",
    re.IGNORECASE,
)


def _classify_confirmation(speech: str) -> Literal["yes_no", "open"] | None:
    """
    Best-effort read on the question a voice turn just left dangling, so
    Android can offer Yes/No buttons instead of only a bare text box.
    Heuristic, not a model decision: the model's final turn is plain speech
    (Section 5.3's text field), nothing structured to read here.

    Isolates the sentence ending in the *last* '?' (a trailing non-question
    clause, e.g. "...or check travel time to it? If you'd like me to add it,
    I'll need a time.", is common and not itself the live question) and
    checks whether it opens with a yes/no auxiliary. A sentence offering an
    alternative ("...to your calendar, or check travel time to it?") is a
    choice, not a yes/no question, even when it opens with "are you" - the
    ' or ' check is what tells those two apart. None if the turn didn't end
    on a question at all.
    """
    last_q = speech.rfind("?")
    if last_q == -1:
        return None
    boundary = max(speech.rfind(".", 0, last_q), speech.rfind("!", 0, last_q))
    question = speech[boundary + 1 : last_q].strip()
    if " or " in question.lower():
        return "open"
    return "yes_no" if _YES_NO_LEAD_IN.match(question) else "open"


# --- the loop ---------------------------------------------------------------

def _recent_episodes(event: Event) -> list[dict[str, Any]]:
    """
    The last RECENT_EPISODES Episodes of this event's type, oldest first, as
    short-term context for the model.

    Asks for one more than it needs: main.py opens this turn's Episode before
    calling run(), so the newest row back is almost always the current event -
    already in the payload as `event`, and not history. Dropping it here costs a
    row rather than a second query.

    Each row is redacted the same way the current turn's payload is - see
    _for_model_episode. History is part of the payload, so anything the model must
    not see now it must not see in history either.
    """
    try:
        rows = memory.recent(event.type, RECENT_EPISODES + 1)
    except Exception as e:
        print(f"[memory] read skipped: {e}")
        return []

    current_id = str(event.id)
    past = [r for r in rows if (r.get("event") or {}).get("id") != current_id]
    print(f"[memory] {len(past)} past {event.type!r} episodes, using last {RECENT_EPISODES}")
    return [_for_model_episode(r) for r in past[-RECENT_EPISODES:]]


def _for_model_episode(row: dict[str, Any]) -> dict[str, Any]:
    stored_state = dict(row.get("user_state") or {})
    offset = stored_state.get("utc_offset_minutes")
    offset = offset if isinstance(offset, int) else 0

    for key in ("current_events", "upcoming_events"):
        entries = stored_state.get(key)
        if isinstance(entries, list):
            stored_state[key] = [dict(e) for e in entries if isinstance(e, dict)]
            _localize_calendar_events(stored_state[key], offset)

    return {
        "created_at": row.get("created_at"),
        "event": _strip_utc_fields(dict(row.get("event") or {})),
        "user_state": _strip_utc_fields(stored_state),
        "action": _redact_control_trace(row.get("action")),
        "outcome": row.get("outcome"),
    }


def _redact_control_trace(action_column: Any) -> Any:
    if not isinstance(action_column, dict):
        return action_column
    return {
        **{k: v for k, v in action_column.items() if k not in ("actions", "calls", "tool", "params")},
        "actions": [
            a.model_copy(update={"error": None, "authority": None, "gain": None}).for_wire()
            for a in Action.from_episode(action_column)
        ],
    }


def _relevant_persona(event: Event) -> list[dict[str, Any]]:
    """
    Long-term facts about the user, retrieved by meaning for this event.

    The other half of what recent_episodes does. recent_episodes is the last
    few raw episodes OF THIS EVENT TYPE - short-term, narrow, and it scrolls:
    the five bagel-shop trips fall out of it after five more navigations, and
    with them any way to answer "what's my usual?". Persona is where that habit
    lives once app/consolidation has counted it, and a vector search finds it
    however the user phrases the question.

    The returned dicts carry the evidence block through as `metadata`, because
    the Observer reads the counted Trends out of it (trends_from_facts) as well as
    the model reading the prose. One search, two readers.

    Non-fatal, like every other store read here: no Supabase, no embedding
    model, or simply nothing stored yet all mean the turn runs without it.
    """
    query = _persona_query(event)
    if not query:
        return []

    try:
        matches = persona.search(persona.PersonaQuery(
            text=query,
            limit=PERSONA_HITS,
            min_similarity=PERSONA_MIN_SIMILARITY,
        ))
    except Exception as e:
        print(f"[persona] search skipped: {e}")
        return []

    print(f"[persona] {len(matches)} fact(s) for {query[:40]!r}")
    return [
        {
            "text": m.fact.text,
            "category": m.fact.category,
            "confidence": m.fact.confidence,
            # Stated vs worked-out. The prompt leans on this to decide which
            # wins when a derived habit and something the user said disagree.
            "source": (m.fact.metadata or {}).get("source", "stated"),
            "support": (m.fact.metadata or {}).get("support"),
            "similarity": round(m.similarity, 3),
            "metadata": m.fact.metadata or {},
        }
        for m in matches
    ]


def _for_model(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The persona payload without the evidence block.

    `metadata` is carried for the Observer's benefit, not the model's - the
    counted signal name and value are machinery, and putting them in the prompt
    invites NOVA to talk about its own internals.
    """
    return [{k: v for k, v in fact.items() if k != "metadata"} for fact in facts]


def _persona_query(event: Event) -> str:
    """What to embed for this event. Voice events carry the user's own words;
    the ambient ones are described by whatever names their subject."""
    parts = [
        getattr(event, "text", None),                       # voice
        getattr(event, "app", None),                        # notification
        getattr(event, "title", None),
        getattr(event, "calendar_event_name", None),        # calendar_trigger
        getattr(event, "calendar_event_location", None),
    ]
    return " ".join(str(p) for p in parts if p).strip()


def run(
    user_state: UserState, event: Event, episode_id: str | None = None
) -> IntentResult | NeedMoreResult:
    if MOCK_LLM:
        text = getattr(event, "text", None)
        return IntentResult(
            event_id=event.id,
            speech=f"[mock] received event type={event.type!r}"
                   + (f" text={text!r}" if text else ""),
            actions=[],
            episode_id=episode_id,
        )

    facts = _relevant_persona(event)

    # --- the control loop, before the model runs -----------------------------
    # In this order, and all of it deterministic. By the time Claude is called,
    # what may happen this turn is already settled; the model's job is to choose
    # parameters and words.
    observation = observe(event, user_state, trends=trends_from_facts(facts))
    command = classify(event)
    turn = _CONTROLLER.open_turn(observation, command)
    authorised = turn.authorised()
    print(f"[control] predicted={observation.predicted.value} "
          f"confidence={observation.prediction_confidence:.2f} "
          f"command={command is not None} authorised={authorised!r}")

    # event.timestamp is always UTC; local_time is that same instant converted

    local_time = event.timestamp + timedelta(minutes=user_state.utc_offset_minutes)
    user_state_dump = user_state.model_dump(mode="json")
    _localize_calendar_events(user_state_dump.get("current_events", []), user_state.utc_offset_minutes)
    _localize_calendar_events(user_state_dump.get("upcoming_events", []), user_state.utc_offset_minutes)

    payload = {
        "event": _strip_utc_fields(event.model_dump(mode="json")),
        "user_state": _strip_utc_fields(user_state_dump),
        "local_time": local_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "recent_episodes": _recent_episodes(event),
        # Short-term above, long-term here: the detail of the last few similar
        # events, plus the durable facts consolidation has distilled out of all
        # of them. "Parked on level 3" only ever appears in the first.
        "persona": _for_model(facts),
    }

    is_voice = event.type == "voice"
    # Only voice turns can be "yes"/"no" answers to a prior spoken question,
    # so only voice turns consume the pending thread - an ambient event
    # arriving in between (location update, notification, ...) must not
    # steal or clear it.
    carried = _pop_pending_confirmation() if is_voice else None
    new_turn: dict[str, Any] = {"role": "user", "content": json.dumps(payload)}
    messages: list[dict[str, Any]] = [*carried, new_turn] if carried else [new_turn]
    if carried:
        print("[loop] continuing pending confirmation thread")

    ctx = TurnContext(
        turn=turn,
        location_ctx=user_state.location_ctx,
        utc_offset_minutes=user_state.utc_offset_minutes,
        episode_id=episode_id,
    )
    return _run_loop(messages, MAX_ITERATIONS, event.id, ctx, is_voice=is_voice)


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
    # The same TurnContext the turn started with, so the Controller's decisions,
    # the refusals and the Actions carry across the hop to the device and back.
    # The authorisation comes with it, because it is part of the Turn: a hop is the
    # middle of one turn, and one turn is one situation, so re-deciding here would
    # let a dial moved while the phone was answering change what NOVA is allowed to
    # finish saying.
    return _run_loop(
        messages, MAX_ITERATIONS, pending["event_id"], pending["ctx"],
        is_voice=pending.get("is_voice", True),
    )


def _run_loop(
    messages: list[dict[str, Any]],
    iterations_left: int,
    event_id: UUID,
    ctx: TurnContext,
    is_voice: bool = False,
) -> IntentResult | NeedMoreResult:
    # Read off the Turn rather than passed in. 
    tools = _build_tools(ctx.turn.authorised())

    # iterate until an appropriate answer is reached
    for _ in range(iterations_left):
        # call model
        print(f"[loop] tools={[t['name'] for t in tools]!r}")
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=tools,
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
            print(f"[loop] final speech={speech!r} actions={ctx.ran!r}")
            # A voice turn that ends by asking a question is left dangling on
            # purpose: stash the real thread (assistant question included) so
            # a same-topic "yes" a moment later continues it verbatim instead
            # of being reconstructed from this episode's logged outcome text.
            # Anything else (a statement, an ambient event) clears/skips it -
            # see _stash_pending_confirmation / _pop_pending_confirmation.
            confirmation = _classify_confirmation(speech) if is_voice else None
            if is_voice:
                if confirmation is not None:
                    _stash_pending_confirmation(
                        [*messages, {"role": "assistant", "content": response.content}]
                    )
                else:
                    _clear_pending_confirmation()
            return IntentResult(
                event_id=event_id, speech=speech, actions=ctx.for_wire(),
                episode_id=ctx.episode_id, confirmation=confirmation,
            )

        # if a tool is called
        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            # A client tool goes through the same check as everything else before
            # the backend pauses and calls out to the phone - it is a registered
            # Function tool with a dial like the others. If it was not authorised,
            # fall through and let the block below hand back the refusal instead
            # of hopping to the device.
            client_call = next(
                (b for b in response.content if b.type == "tool_use" and b.name in CLIENT_TOOLS),
                None,
            )
            if client_call is not None and _gate(client_call.name, ctx) is None:
                _record_action(client_call.name, client_call.input, ctx, ran=True)
                session_id = str(uuid.uuid4())
                _PENDING_SESSIONS[session_id] = {
                    "messages": messages,
                    "tool_use_id": client_call.id,
                    "event_id": event_id,
                    "ctx": ctx,
                    # Carried so resume() can localise whatever the phone sends
                    # back. Without it the far side of the hop defaults to UTC,
                    # and the calendar comes back a timezone out.
                    "utc_offset_minutes": ctx.utc_offset_minutes,
                    "is_voice": is_voice,
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
                    blocked = _gate(block.name, ctx)
                    if blocked is not None:
                        # Refused calls are recorded here; allowed ones are
                        # recorded by _run_local_tool once they return.
                        _record_action(block.name, block.input, ctx, ran=False)
                    result = blocked if blocked is not None else _run_local_tool(
                        block.name, block.input, ctx
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    })
            messages.append({"role": "user", "content": tool_results})
            continue

        # Server-side tool loop (web_search) hit its internal iteration cap.
        # Resend as-is - the trailing server_tool_use block tells the API to
        # pick up where it left off. No synthetic "continue" message; adding
        # one would just be extra text the model has to read past.
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue

        # unexpected stop_reason (max_tokens, refusal, ...)
        print(f"[loop] breaking on unexpected stop_reason={response.stop_reason!r}")
        break

    print("[loop] exited loop with no end_turn - returning empty speech")
    return IntentResult(
        event_id=event_id, speech="", actions=ctx.for_wire(),
        episode_id=ctx.episode_id,
    )
