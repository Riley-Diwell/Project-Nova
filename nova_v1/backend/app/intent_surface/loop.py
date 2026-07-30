"""
intent_surface/loop.py - Section 5.3: Intent Surface  (Georgia)

STATUS: working draft

WHAT THIS FILE IS
Uses AI to infer intent. 
    1. Runs an Anthropic client that receives a user message, an event ID 
    and some situational context. 
    2. Runs a tool calling loop to process this data and infer intent. 

WHO USES THIS
- Georgia: main.py's /event handler will call run(user_state) 
"""

# import necessary libraries
import json
import os
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
from gain.config import FIRING_THRESHOLD
from gain.gain_store import GainStore
from gain.overrides import GainOverrides
from gain.reinforcement import Outcome, Reinforcer
from tools.registry import ToolRegistry
from tools.dispatcher import Dispatcher
from tools.calendar_tool import AddCalendarEventTool, CalendarTool
from tools.memory_tool import MemoryTool
from tools.navigation import NavigationTool
from tools.notification_management import NotificationManagementTool

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
    "Not everything the user says is a request. A plain statement, an "
    "observation, something said in passing - these are not requests you have "
    "failed to fulfil, so do not answer them by asking what they wanted. Run "
    "them past the PROACTIVITY rules below first; often the right response to "
    "a statement is to quietly do something with it and say little or "
    "nothing. "
    "CALENDAR AND TIME. Now is the top-level local_time; every event carries "
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
    "PROACTIVITY. Every tool that does something for the user takes a "
    "`trigger`, and you must set it truthfully on every call. 'requested' "
    "means this turn's words asked for it. 'inferred' means you decided it "
    "would help - volunteering something unasked, following a hunch or a "
    "pattern, or adding context to a question about something else. When in "
    "doubt it is 'inferred'. Each tool's description tells you its current "
    "controller gain: that is the user's own setting for how much this tool "
    "may do without being asked, and an inferred call below it is refused and "
    "does nothing. "
    "A high gain is an instruction, not merely permission: a tool near 1.00 "
    "means act on inference without being asked, whether or not the moment "
    "seems to warrant it. Weighing that up is what a low gain is for. Past "
    "episodes where nothing was done are not evidence that nothing should be "
    "done now - they predate the current settings. "
    "Decide all of this silently. Your text field is speech, not a place to "
    "think: it holds only words a person would say out loud, and never your "
    "reasoning, your weighing up, or any mention of gain, tools or why you "
    "did or did not act. If the answer is to act quietly, act and leave the "
    "text empty. "
    "Respect a refusal - answer what they asked and drop what you were going "
    "to add. Never relabel a refused call as 'requested' to get around it, "
    "and never tell the user about gain, tools or refusals; they should "
    "simply notice Nova offering more or less unprompted. A low gain never "
    "stops you doing what you were directly asked to do. "
    "recent_episodes is inferred from behaviour; the memory tool is the "
    "separate notebook of what the user has told you. Always save when they "
    "ask you to remember or note something, and recall when they ask what "
    "they told you or what they noted - that much is true at any gain. "
    "Beyond that floor, how much you file and look up unasked is set by the "
    "memory tool's own gain, not by this paragraph: at a high gain, saving a "
    "statement they simply made in passing is exactly what they have asked "
    "for. When both bear on an answer, prefer what they stated over what you "
    "inferred. "
    "persona is the third source and the long-term one: durable facts about "
    "who this user is, retrieved by meaning for this particular event. Some "
    "were stated outright; the ones marked source 'derived' NOVA worked out "
    "from repeated behaviour, and those carry a confidence and the number of "
    "times it was seen. Treat them as established background about the user "
    "rather than as anything they just said - they are true in general, not "
    "necessarily true right now. Use them to answer about habits, usuals and "
    "preferences without needing recent_episodes to still contain the "
    "evidence. Where a derived fact and something the user actually stated "
    "disagree, the stated one wins."
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
# Also runs on the phone, but nothing comes back, so it needs no pause - the
# Action it records is itself the instruction the device executes.
_REGISTRY.register(AddCalendarEventTool())
_DISPATCHER = Dispatcher(_REGISTRY)

CLIENT_TOOLS: set[str] = {"get_calendar_range"}

# Every Function tool carries this. It is what makes gain mean anything: the
# model has to declare, per call, whether the user asked for this or whether it
# is acting on its own reading of the situation. "requested" runs regardless of
# gain (Section 5.7: gain governs inferred intent only); "inferred" has to clear
# gain x state_confidence. Context tools (get_current_address) have no gain and
# so no trigger.
TRIGGER_PROPERTY: dict[str, Any] = {
    "type": "string",
    "enum": ["inferred", "requested"],
    "description": (
        "How this call was triggered. Apply this as a test of the user's "
        "GRAMMAR, not of their intent or of how useful the call would be. "
        "'requested' requires that their words this turn were an imperative "
        "('remember this', 'tell me what's on') or a question ('where did I "
        "park?', 'am I free then?') asking for this. "
        "'inferred' is everything else, and in particular EVERY bare "
        "declarative statement. 'I always park on level 4', 'trees blow in "
        "the wind', 'the draft is due Friday' are statements, not requests - "
        "saving them may well be the right thing to do, but it is your "
        "decision to do it, so it is 'inferred'. A statement does not become "
        "a request because it is useful to act on, because it is addressed to "
        "you, or because a high gain means you are going to act on it anyway. "
        "If there is no imperative and no question mark, it is 'inferred'. "
        "This is the user's own control over how much Nova does unasked; "
        "calling an inferred action 'requested' takes that control away from "
        "them."
    ),
}


def _tool_definition(name: str, state_confidence: float) -> dict[str, Any]:
    """
    One registered Function tool as the Anthropic API wants it, with the trigger
    field injected and the current gain spelled out in the description.

    The gain is written into the description rather than passed as a field
    because the API ignores unknown keys - the model only ever reads the prose.
    Section 6.3's frozen schema still carries `gain`; this is that number
    reaching the model in the one form it can actually act on.
    """
    schema = _REGISTRY.get_schema(name)
    tool = _REGISTRY.get_tool(name)

    gain = schema.gain
    will_fire = state_confidence * gain >= FIRING_THRESHOLD

    outcome = (
        "will be allowed."
        if will_fire
        else (
            "will be BLOCKED and will do nothing - at this gain the user only "
            "wants this tool when they ask for it, so do not volunteer it "
            "unprompted."
        )
    )
    guidance = "\n\n" + " ".join(
        part
        for part in (
            f"PROACTIVITY (controller gain = {gain:.2f} of 1.00).",
            tool.gain_description,
            f"Right now the user's state confidence is {state_confidence:.2f}, "
            f"so an 'inferred' call to this tool {outcome}",
        )
        if part
    )

    input_schema = {
        **schema.input_schema,
        "properties": {**schema.input_schema.get("properties", {}), "trigger": TRIGGER_PROPERTY},
        "required": [*schema.input_schema.get("required", []), "trigger"],
    }

    return {
        "name": schema.name,
        "description": schema.description + guidance,
        "input_schema": input_schema,
    }


def _build_tools(state_confidence: float) -> list[dict[str, Any]]:
    """
    The tool list for one turn. Built per turn, not once at import, because the
    user can move a dial mid-session (PUT /tools/gain) and because the gain
    guidance above is relative to *this* turn's state confidence.

    get_current_address is a context tool - a lookup with no gain, so it stays
    out of the registry (see dispatcher.py) and is declared inline above.
    Everything else Claude can call comes from the registry, so each one has a
    dial and each one is gated.
    """
    return [
        GET_CURRENT_ADDRESS_TOOL,
        *(_tool_definition(name, state_confidence) for name in _REGISTRY.all_names()),
    ]

# The registry is built here, so this is where the gain package gets pointed at
# it. main.py's GET/PUT /tools/gain go straight through this - the tuning logic
# itself lives in gain/overrides.py, next to the reinforcement that moves the
# same numbers from the other direction.
GAIN_OVERRIDES = GainOverrides(_REGISTRY, _GAIN_STORE)
_REINFORCER = Reinforcer(_REGISTRY, _GAIN_STORE)


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
    for action in (episode.get("action") or {}).get("actions") or []:
        name = action.get("tool")
        if not action.get("ran") or not name or name in moved:
            continue
        try:
            moved[name] = _REINFORCER.reinforce(name, verdict)
        except KeyError:
            # Not a registered Function tool - nothing to tune.
            continue
    return moved


@dataclass
class TurnContext:
    """
    What the gate and the tools need to know about the turn in progress, kept
    in one object so it can be threaded through the loop and parked in
    _PENDING_SESSIONS across a client-tool hop without growing an argument list
    every time something new is needed.
    """

    location_ctx: str | None
    state_confidence: float

    # Minutes east of UTC for this turn's user_state, kept so a client-tool hop
    # can localise what the phone sends back on the far side of the pause.
    utc_offset_minutes: int = 0

    # Tools whose inferred call was blocked this turn. Re-checked before every
    # dispatch so the model cannot get a second bite by relabelling the same
    # call "requested" after being told the first one was suppressed.
    suppressed: set[str] = field(default_factory=set)

    # Every Function-tool call this turn, in order, with the parameters the
    # model resolved - {tool, input, trigger, ran}. ONE structure serves three
    # readers (CONTEXT.md "Action"): it goes out as EventOut.actions for the
    # phone to execute and display, and it is written to the episode's `action`
    # column for consolidation to count. It used to be two things - a list of
    # names for the wire and a parallel list of dicts for Memory - which is how
    # the wire ended up carrying names the client could not parse.
    #
    # The resolved parameters are the point. The destination "Brooklyn Boy
    # Bagels" exists nowhere else: the user's speech says "the bagel place", and
    # five different phrasings of the same ask converge on one entity only at
    # the moment the tool is called.
    actions: list[dict[str, Any]] = field(default_factory=list)

    # The episodic_memory row main.py opened for this turn, carried so the row
    # can be closed once the turn resolves - which may be on the far side of a
    # client-tool hop, in /event/continue rather than /event.
    episode_id: str | None = None

    @property
    def ran(self) -> list[str]:
        """Names of the tools that actually ran - logging only."""
        return [a["tool"] for a in self.actions if a["ran"]]


def _gate(name: str, tool_input: dict[str, Any], ctx: TurnContext) -> dict[str, Any] | None:
    """
    Decide whether this tool call is allowed to happen.

    Returns None to allow it, or the tool_result content to hand back instead
    of running it. Context tools (no gain) always pass.

    This is the point the Gain tab's dial finally bites: everything upstream
    only stored the number.
    """
    if not _REGISTRY.has(name):
        return None  # context tool - no gain, nothing to gate

    trigger = str(tool_input.get("trigger") or "requested")

    if name in ctx.suppressed:
        # Already refused once this turn; refuse consistently whatever it is
        # labelled now.
        decision_gain = _REGISTRY.get_gain(name).get_effective()
        print(f"[gain] {name} blocked again (already suppressed this turn)")
        return _suppressed_result(name, decision_gain, ctx.state_confidence)

    decision = _DISPATCHER.should_run(name, trigger, ctx.state_confidence)
    print(
        f"[gain] {name} trigger={trigger!r} gain={decision.effective_gain:.2f} "
        f"confidence={decision.state_confidence:.2f} "
        f"product={decision.effective_gain * decision.state_confidence:.2f} "
        f"threshold={FIRING_THRESHOLD:.2f} -> "
        f"{'run' if decision.proposed else 'SUPPRESSED'}"
    )

    if decision.proposed:
        return None

    ctx.suppressed.add(name)
    return _suppressed_result(name, decision.effective_gain, decision.state_confidence)


def _record_action(
    name: str, tool_input: dict[str, Any], ctx: TurnContext, ran: bool
) -> None:
    """
    Record one Action. Context tools are skipped: a reverse-geocode lookup says
    nothing about what the user habitually does, and counting it as behaviour
    would put noise into the trends consolidation derives.

    Suppressed calls are recorded too (ran=False), and they go over the wire as
    well. What NOVA wanted to do and was refused is evidence about the user's
    settings rather than about the user - and the phone needs to be able to tell
    "nothing happened" from "nothing was attempted".
    """
    if not _REGISTRY.has(name):
        return
    ctx.actions.append({
        "tool": name,
        "input": dict(tool_input),
        "trigger": str(tool_input.get("trigger") or "requested"),
        "ran": ran,
    })


def _suppressed_result(name: str, gain: float, confidence: float) -> dict[str, Any]:
    """
    What the model gets back when gain refuses an inferred call. Phrased as a
    result rather than an error because nothing went wrong - the user has
    simply set this tool to stay quiet unless asked.
    """
    return {
        "success": False,
        "suppressed_by_gain": True,
        "effective_gain": round(gain, 2),
        "state_confidence": round(confidence, 2),
        "message": (
            f"Not run. The user has {name} set to a controller gain of "
            f"{gain:.2f}, which is too low to act on inferred intent at the "
            f"current state confidence of {confidence:.2f}. This is the user's "
            f"deliberate setting, not a failure. Do not run this tool again "
            f"this turn, do not relabel the call as 'requested', and do not "
            f"mention the tool, the gain or this refusal to the user - just "
            f"answer what they actually asked, leaving out whatever you were "
            f"going to volunteer."
        ),
    }


def _run_local_tool(name: str, tool_input: dict[str, Any], ctx: TurnContext) -> Any:
    if name == "get_current_address":
        return _reverse_geocode(ctx.location_ctx)
    if _REGISTRY.has(name):
        if ctx.location_ctx and "origin" not in tool_input:
            tool_input = {**tool_input, "origin": ctx.location_ctx}
        # The gain check already happened in _gate; both of these just run the
        # tool, but which one is called records why it was allowed to.
        if str(tool_input.get("trigger") or "requested") == "requested":
            result = _DISPATCHER.dispatch_reactive(name, tool_input)
        else:
            result = _DISPATCHER.confirm_proactive(name, tool_input)
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

    # Every Action this turn took, in order (CONTEXT.md "Action"). The phone
    # executes the ones it recognises; the same list is written to the episode.
    actions: list[dict[str, Any]] = []

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


# --- the loop ---------------------------------------------------------------

def _recent_episodes(event: Event) -> list[dict[str, Any]]:
    """
    The last RECENT_EPISODES Episodes of this event's type, oldest first, as
    short-term context for the model.

    Asks for one more than it needs: main.py opens this turn's Episode before
    calling run(), so the newest row back is almost always the current event -
    already in the payload as `event`, and not history. Dropping it here costs a
    row rather than a second query.
    """
    try:
        rows = memory.recent(event.type, RECENT_EPISODES + 1)
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


def _relevant_persona(event: Event) -> list[dict[str, Any]]:
    """
    Long-term facts about the user, retrieved by meaning for this event.

    The other half of what recent_episodes does. recent_episodes is the last
    few raw episodes OF THIS EVENT TYPE - short-term, narrow, and it scrolls:
    the five bagel-shop trips fall out of it after five more navigations, and
    with them any way to answer "what's my usual?". Persona is where that habit
    lives once app/consolidation has counted it, and a vector search finds it
    however the user phrases the question.

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
        }
        for m in matches
    ]


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
        # Short-term above, long-term here: the detail of the last few similar
        # events, plus the durable facts consolidation has distilled out of all
        # of them. "Parked on level 3" only ever appears in the first.
        "persona": _relevant_persona(event),
    }

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": json.dumps(payload)},
    ]
    ctx = TurnContext(
        location_ctx=user_state.location_ctx,
        state_confidence=user_state.confidence,
        utc_offset_minutes=user_state.utc_offset_minutes,
        episode_id=episode_id,
    )
    return _run_loop(messages, MAX_ITERATIONS, event.id, ctx)


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
    # The same TurnContext the turn started with, so gain decisions and the
    # suppressed/ran lists carry across the hop to the device and back.
    return _run_loop(messages, MAX_ITERATIONS, pending["event_id"], pending["ctx"])


def _run_loop(
    messages: list[dict[str, Any]],
    iterations_left: int,
    event_id: UUID,
    ctx: TurnContext,
) -> IntentResult | NeedMoreResult:
    tools = _build_tools(ctx.state_confidence)

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
            return IntentResult(
                event_id=event_id, speech=speech, actions=ctx.actions,
                episode_id=ctx.episode_id,
            )

        # if a tool is called
        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            # A client tool has to clear the same gate as everything else
            # before the backend pauses and calls out to the phone - it is a
            # registered Function tool with a dial like the others. If gain
            # refuses it, fall through and let the block below hand back the
            # suppressed result instead of hopping to the device.
            client_call = next(
                (b for b in response.content if b.type == "tool_use" and b.name in CLIENT_TOOLS),
                None,
            )
            if client_call is not None and _gate(client_call.name, client_call.input, ctx) is None:
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
                    blocked = _gate(block.name, block.input, ctx)
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

        # unexpected stop_reason (max_tokens, refusal, pause_turn, ...)
        print(f"[loop] breaking on unexpected stop_reason={response.stop_reason!r}")
        break

    print("[loop] exited loop with no end_turn - returning empty speech")
    return IntentResult(
        event_id=event_id, speech="", actions=ctx.actions,
        episode_id=ctx.episode_id,
    )