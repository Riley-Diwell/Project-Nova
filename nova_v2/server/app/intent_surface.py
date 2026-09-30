"""
intent_surface.py - Section 5.3: Intent Surface  (Georgia)

STATUS: working draft

WHAT THIS FILE IS
Uses AI to infer intent.
    1. Calls the model (core/llm.py - the self-hosted Qwen, over the OpenAI
protocol). It receives an Event and the User State the phone computed for it,
and produces the words NOVA says plus the Actions it took.
    2. Runs a tool calling loop to process this data and infer intent.

WHO USES THIS
- main.py's /event and /event/continue handlers call run() and resume().
"""

# import necessary libraries
import json
import os
import re
import uuid
from types import SimpleNamespace
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

import requests
from pydantic import BaseModel

# import nova libraries
from app.schemas.user_state import UserState
from app.schemas.event import Event
from app.control.commands import classify, note_body
from app.control.controller import Decision, ProportionalController, Reason, Turn
from app.control.observer import observe, trends_from_facts
from app.control.gain.gain_store import GainStore
from app.control.gain.overrides import GainOverrides
from app.control.gain.reinforcement import Outcome, Reinforcer
from app.core import llm
from app.core.request_user import as_user
from app.tools.core.action import Action
from app.tools.core.catalogue import CLIENT_TOOLS, DEVICE_TOOLS, build_registry
from app.tools.core.dispatcher import Dispatcher
from app.tools.core.registry import ToolRegistry
from app.tools.functions.notification_management import set_batcher_mode
from app.tools.web_search import WEB_SEARCH_TOOL, web_search
from app.store import canvas as canvas_store
from app.tools.canvas.tool import CANVAS_TOOL, run_canvas

from app.store import memory
from app.store import persona
from app.store import profile

MAX_ITERATIONS = 5

# A voice turn waits on this; the model is local, so a generous timeout still
# fails fast when the box is down.
LOOP_TIMEOUT_S = 45.0

# web_search runs locally now (tools/web_search.py), so its cap is ours to
# enforce - the hosted tool's max_uses did this before.
WEB_SEARCH_MAX_USES = 3
# canvas calls per turn: materials then read, sometimes twice, plus a lookup.
CANVAS_MAX_USES = 6

# How many past Episodes of the same type to hand the model as short-term context.
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
# routing, wire contract) without calling the model - useful for testing
# without the model server running.
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
    "For ambient events - a notification arriving, a calendar trigger - stay "
    "quiet, and quiet means an empty string, never a description of the "
    "event that triggered you. The user never sees the event JSON, only "
    "your words, so do not explain what kind of event happened, that no "
    "action was needed, or that you decided not to interrupt - any sentence "
    "describing your own silence is the same mistake as describing the "
    "event itself (never say things like \"this is a timestamp event with "
    "no user action\" or \"no interruption warranted\" or \"nothing to "
    "report\") - just return \"\". But if the user is speaking to you directly and "
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
    "WHEN TO LEAVE. If the user asks when they need to leave, how long it "
    "takes to get somewhere, or which way to go, without naming a "
    "destination - 'when do I need to leave', 'how do I get to class', 'which "
    "way from here' - take the destination from their next current_events/ "
    "upcoming_events entry's location and its start_local as arrival_time, "
    "rather than asking them to repeat what they're clearly already going to. "
    "Also pass that entry's own minutes_until_start straight through as the "
    "tool's minutes_until_start - copy the number, never convert it - so Nova "
    "can schedule a precise alert for the real leave-by moment rather than "
    "only answering right now. Pass the entry's title the same way, as "
    "event_title - copy it verbatim, never paraphrase or invent one. Leave "
    "both out for a destination with no calendar anchor. "
    "If that entry has no location, say you don't have one for it rather than "
    "guessing. If navigation_departure_time says it doesn't have the user's "
    "location, tell them that - never give a travel time or directions "
    "anyway, from a guessed starting point or from general knowledge. "
    "This also covers 'which way do I walk' when they're already "
    "there - call navigation_departure_time with mode 'walking'. On an ambient "
    "event (nobody spoke - a timer or a location change), call "
    "navigation_departure_time for the next commitment when one is available "
    "- but leave speech empty regardless of what it reports (imminent, "
    "comfortable, already arrived, whatever). The device builds the leave-soon "
    "nudge itself from the tool's own numbers, not from anything you say, so "
    "there is nothing to phrase - just call the tool and return \"\". Only "
    "answer about leaving out loud when the user asked directly (a voice turn). "
    "UNIVERSITY. When a canvas tool is offered, use it for coursework: what's "
    "due, marks and feedback, and what a class covers - the calendar says when "
    "a class is, Canvas says what's in it. "
    "TIMERS AND ALARMS. Call set_timer for a bare countdown with no clock "
    "time and nothing to say - 'set a timer for 10 minutes', 'ping me in 90 "
    "seconds' - "
    "converting whatever they said into whole duration_seconds yourself (10 "
    "minutes -> 600). Call set_alarm instead when they name a clock time - "
    "'wake me up at 7', 'set an alarm for 6:30am' - using hour/minute in the "
    "user's LOCAL time (top-level local_time), never UTC. Both fire on the "
    "device the moment the call is made, so confirm them in speech as done, "
    "not as pending. "
    "REMINDERS. A reminder has something to say ('remind me to email Dr Chen "
    "at 4:30', 'remind me in 20 minutes to take the pasta off', 'don't let me "
    "forget to submit the form') - call set_reminder. A timer is a bare "
    "countdown, an alarm is a wake-up, and something with a place, people or "
    "a duration is a calendar event. For a relative time pass in_minutes and "
    "never add it to local_time yourself. 'After this lecture' or 'after "
    "class' means copying that current_events entry's end_local exactly. If "
    "they gave no time, ask when. user_state.reminders lists their reminders "
    "for the next week and any that just went off - answer questions about "
    "them from there, and call get_reminders for anything outside it. "
    "update_reminder completes, snoozes, edits or deletes one, and its "
    "reminder_id must be copied from user_state.reminders or a get_reminders "
    "result, never invented. 'That' or 'it' just after a reminder went off "
    "means the one with the smallest fired_minutes_ago. "
    "CHANGING A REMINDER. 'Change my dentist reminder to 4pm', 'move the "
    "form reminder to Friday', 'make the Sam one say call Sam instead' - find "
    "the reminder whose text matches their words in user_state.reminders; if "
    "none does, call get_reminders from local_time to a year ahead to look "
    "for it before saying you can't find it. If two or more match, ask which "
    "one. Then call update_reminder with action 'edit'. A new clock time "
    "keeps the reminder's own date (read it off its due_local) and a new day "
    "keeps its own time, both passed as a full due_local; 'an hour later' or "
    "'push it back' is shift_minutes; new wording is text. Never create a "
    "second reminder instead of editing, and never delete and re-add. "
    "Reminders are stored on the device the moment the call is made, so "
    "confirm them as done. "
    "EDITING OR DELETING A CALENDAR EVENT both need its event_id, which only "
    "ever comes from a get_calendar_range result (this turn's or one in "
    "recent_episodes) - call get_calendar_range first if you don't already "
    "have the event_id for what the user means, and never invent one. An edit "
    "applies immediately like an add, so confirm it in speech as done. A "
    "delete does not - the phone always confirms with the user before "
    "actually deleting, so speak about that one as pending, not as done. "
    "THREE SOURCES, DIFFERENT LIFETIMES. recent_episodes is the last few logged "
    "episodes of this event type, oldest first - what happened recently, "
    "including the situational detail that stops mattering on its own ('parked "
    "on level 3'). Use it to stay consistent with what you just did. When the "
    "user says 'that', 'it' or otherwise refers to something without naming "
    "it again, check the most recent entry in recent_episodes for what they "
    "said before claiming you have no context - the words are there even on "
    "turns where nothing was saved to memory or persona for them. "
    "standing_instructions, when present, are what the user has told you about "
    "how to behave and talk to them - they apply to every reply, whatever it is "
    "about, including how you phrase your speech. "
    "The memory tool is the notebook of what the user has told you: save when "
    "they ask you to remember or note something, and recall when they ask what "
    "they told you. "
    "persona is the long-term one: durable facts about who this user is, "
    "retrieved by meaning for this event. These are how you answer about "
    "habits, usuals and preferences - established background rather than "
    "anything just said, true in general rather than necessarily true right "
    "now. The ones marked source 'derived' NOVA worked out by counting repeated "
    "behaviour and carry how many times it was seen. Conflicting facts are "
    "resolved before you see them - whichever the user said or did most "
    "recently is kept - so treat each one as current."
)


# --- local tools -------------------------------------------------------------

# web_search (tools/web_search.py) and get_current_address are context tools:
# answered here, never gated, never recorded as Actions.
CONTEXT_TOOLS = ("web_search", "get_current_address", "canvas")

GET_CURRENT_ADDRESS_TOOL: dict[str, Any] = {
    "name": "get_current_address",
    "description": (
        "Reverse-geocodes the user's current coordinates (from their "
        "location_ctx in user_state) into a human-readable address. Call "
        "this when the user asks where they are - never speak raw "
        "lat/lng coordinates aloud. On success, relay the result's own "
        "'spoken' line rather than composing your own - it is a GPS fix "
        "snapped to the nearest known address, not a confirmed exact "
        "location, so it is phrased as 'near', never 'at'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
    },
}

# Which tools exist, and which of them resolve on the phone, live in
# tools/core/catalogue.py - so the Controller and its tests can ask what NOVA
# can do without importing a model client.
_GAIN_STORE = GainStore()
_REGISTRY = build_registry(_GAIN_STORE)
_DISPATCHER = Dispatcher(_REGISTRY)

def _tool_definition(name: str) -> dict[str, Any]:
    """
    One registered Function tool, as the OpenAI tools list wants it.

    Just the tool: its name, what it does, and what parameters it takes. No
    `trigger` field, because whether the user asked is settled before this runs.
    No gain number and no proactivity guidance in the description either - the
    model is not weighing up whether to act, so telling it the dial position
    would only invite it to.
    """
    schema = _REGISTRY.get_schema(name)


    return _as_function(schema.name, schema.description, schema.input_schema)


def _as_function(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }

def _build_tools(authorised: list[str], canvas: bool = False) -> list[dict[str, Any]]:
    """
    The tool list for one turn: exactly what the Controller authorised, plus the
    context tools - canvas only for a user who has connected it, so nobody else's
    prompt grows by a tool they can't use.

    A Function tool with no authority this
    turn is simply absent, so calling it is structurally impossible rather than
    prose-discouraged - there is no refusal for the model to argue with,
    relabel, or accidentally explain to the user.

    get_current_address stays out of the Controller's hands: it is a context tool,
    a lookup with no gain, and gating a reverse-geocode would be gating NOVA's
    ability to answer "where am I?" (see dispatcher.py).
    """
    return [
        *(_as_function(t["name"], t["description"], t["input_schema"])
          for t in (WEB_SEARCH_TOOL, GET_CURRENT_ADDRESS_TOOL)),
        *(_tool_definition(name) for name in authorised),
        # Last, so the shared prefix above stays identical for every user.
        *([_as_function(CANVAS_TOOL["name"], CANVAS_TOOL["description"], CANVAS_TOOL["input_schema"])]
          if canvas else []),
    ]

# The registry is built here, so this is where the gain package gets pointed at
# it. _REGISTRY holds the tools and the seed's starting gains; every user has
# their own dials on top, so each turn,
# outcome and Gain-tab request works on a view carrying that user's gains, read
# fresh from the table. main.py's GET/PUT /tools/gain go through gain_overrides()
# - the tuning logic itself lives in control/gain/overrides.py, next to the
# reinforcement that moves the same numbers from the other direction.
def _gains_for(user_id: UUID | str) -> tuple[ToolRegistry, GainStore]:
    """This user's view of the registry, and the store their changes save to."""
    store = _GAIN_STORE.for_user(user_id)
    return _REGISTRY.with_gains(store.load_all()), store


def gain_overrides(user_id: UUID | str) -> GainOverrides:
    """The Gain tab's reads and writes, for this user's dials."""
    return GainOverrides(*_gains_for(user_id))


def reinforce_episode(user_id: UUID | str, episode_id: str, outcome: str) -> dict[str, float]:
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

    Only this user's Episode, and only their dials: someone else's episode id
    reads as no such episode, and nothing moves.
    """
    try:
        verdict = Outcome(outcome)
    except ValueError:
        print(f"[gain] ignoring unknown outcome {outcome!r}")
        return {}

    try:
        episode = memory.get(user_id, episode_id)
    except Exception as e:
        print(f"[gain] reinforcement skipped, episode unreadable: {e}")
        return {}
    if episode is None:
        print(f"[gain] reinforcement skipped, no such episode {episode_id!r}")
        return {}

    reinforcer = Reinforcer(*_gains_for(user_id))
    moved: dict[str, float] = {}
    for action in Action.from_episode(episode.get("action")):
        if not action.ran or action.tool in moved:
            continue
        try:
            moved[action.tool] = reinforcer.reinforce(action.tool, verdict)
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

    # Whose turn this is - from the verified token, via main.py. Set again as
    # the request user (core/request_user.py) on the far side of a client-tool
    # hop, and checked against the caller there (see resume()).
    user_id: UUID | str | None = None

    location_ctx: str | None = None

    # The user's declared travel-mode preference (Settings), injected into a
    # tool call's "mode" the same way location_ctx becomes "origin" below -
    # so "when do I leave for class" doesn't require the model to guess how
    # this particular user gets around.
    preferred_travel_mode: str | None = None

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

    # Set when navigation_departure_time ran this turn and returned a
    # leave_in_minutes - {destination, mode, leave_in_minutes,
    # minutes_until_start, event_title}. Carried separately from `actions`
    # because that records the tool's *input*, not its *result*, and this is
    # the one result Android needs structured rather than folded into speech
    # (see the tool loop below) - it builds the leave-soon notification text
    # itself from these fields instead of trusting the model to phrase it.
    scheduled_departure: dict[str, Any] | None = None

    # The reminder ids the model has actually been shown this turn - the
    # user_state.reminders window plus any get_reminders result. None when the
    # phone sent no window at all (an older client), which turns the check in
    # _unknown_reminder off rather than rejecting every update_reminder.
    known_reminder_ids: set[str] | None = None

    # web_search calls so far this turn, against WEB_SEARCH_MAX_USES.
    web_searches: int = 0

    # Whether this user has connected Canvas (store/canvas.py), which is what
    # puts the canvas tool on offer; and its calls so far, against CANVAS_MAX_USES.
    canvas: bool = False
    canvas_calls: int = 0

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
        reason=decision.reason.value,
        **decision.trace(),
    ))


def _memory_outcome(result: Any) -> dict[str, Any]:
    """Where a memory save landed, for its Action: long-term memory (a Persona
    fact, and whether it was new, a duplicate or a replacement) or a note. The
    audit log words the two differently - they are different features."""
    if not isinstance(result, dict) or result.get("success") is not True:
        return {"failed": True}
    if result.get("fact_id"):
        return {"saved_as": "memory", "outcome": result.get("outcome")}
    if result.get("note_id"):
        return {"saved_as": "note"}
    return {}


# What a canvas Action keeps of the call - never the tool's result text.
_CANVAS_INPUT_KEYS = ("action", "course", "days", "assignment", "query", "item", "question")


def _record_canvas(tool_input: dict[str, Any], result: Any, ctx: TurnContext) -> None:
    """Record a canvas call as an Action, for the audit log.

    The one context tool that is recorded: it reads the user's coursework and
    grades, which is exactly the kind of thing the audit log exists to show. A
    read keeps the title of the document it opened, so the log can say "Read
    'Tutorial 11.docx'" rather than just "checked Canvas". No gain and no
    control trace - nothing decided whether it could run - and consolidation
    counts nothing from it (trends.COUNTED_ACTION_FIELDS has no canvas entry).
    The phone skips Actions for tools it doesn't carry out.
    """
    recorded = {k: tool_input[k] for k in _CANVAS_INPUT_KEYS if tool_input.get(k) not in (None, "")}
    ok = isinstance(result, dict) and result.get("success") is True
    if ok and tool_input.get("action") == "read":
        recorded["document"] = result.get("title")
        recorded["document_type"] = result.get("type")
    if ok and result.get("course"):
        recorded["course_name"] = result["course"]
    ctx.actions.append(Action(tool="canvas", input=recorded, trigger="requested", ran=ok, reason="context"))


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


def _unknown_reminder(name: str, tool_input: dict[str, Any], ctx: TurnContext) -> dict[str, Any] | None:
    """The refusal for an update_reminder naming an id the model was never
    shown, or None to let it through.

    The phone treats an unknown id as a silent no-op, and by then the model
    has already said "done" - so a guessed or stale id has to be caught here,
    where the model can still recover by looking the reminder up.
    """
    if name != "update_reminder" or ctx.known_reminder_ids is None:
        return None
    if str(tool_input.get("reminder_id")) in ctx.known_reminder_ids:
        return None
    return {
        "success": False,
        "error": (
            "That reminder_id isn't in user_state.reminders or a get_reminders "
            "result this turn. Call get_reminders (from local_time to a year "
            "ahead) to find the reminder they mean, then use its id."
        ),
    }


def _run_local_tool(name: str, tool_input: dict[str, Any], ctx: TurnContext) -> Any:
    if name == "get_current_address":
        return _reverse_geocode(ctx.location_ctx)
    if name == "web_search":
        if ctx.web_searches >= WEB_SEARCH_MAX_USES:
            return {"success": False, "error": (
                "search limit reached this turn - answer from the results you have")}
        ctx.web_searches += 1
        return web_search(str(tool_input.get("query", "")))
    if name == "canvas":
        if not ctx.canvas:
            return {"success": False, "error": "Canvas isn't connected"}
        if ctx.canvas_calls >= CANVAS_MAX_USES:
            return {"success": False, "error": (
                "Canvas limit reached this turn - answer from what you have")}
        ctx.canvas_calls += 1
        try:
            connection = canvas_store.get(ctx.user_id)
        except Exception as e:
            print(f"[canvas] connection lookup failed: {e}")
            return {"success": False, "error": "Canvas is unavailable right now"}
        result = run_canvas(tool_input, connection, ctx.utc_offset_minutes)
        _record_canvas(tool_input, result, ctx)
        return result
    unknown = _unknown_reminder(name, tool_input, ctx)
    if unknown is not None:
        _record_action(name, tool_input, ctx, ran=False)
        return unknown
    if _REGISTRY.has(name):
        # Not for DEVICE_TOOLS: a reminder has no use for the user's
        # coordinates, and whatever is injected here is written into the Action
        # and so into the Episode log.
        if ctx.location_ctx and "origin" not in tool_input and name not in DEVICE_TOOLS:
            tool_input = {**tool_input, "origin": ctx.location_ctx}
        if ctx.preferred_travel_mode and "mode" not in tool_input and name not in DEVICE_TOOLS:
            tool_input = {**tool_input, "mode": ctx.preferred_travel_mode}
        if "utc_offset_minutes" not in tool_input:
            # So a tool that needs to interpret a user-relative clock string (e.g.
            # navigation_departure_time's arrival_time) can convert it against the
            # user's own wall clock instead of the server process's - see
            # navigation.py's _query_google_maps/_estimate_without_api.
            tool_input = {**tool_input, "utc_offset_minutes": ctx.utc_offset_minutes}
        if name == "memory" and ctx.episode_id and "episode_id" not in tool_input:
            # A durable "remember..." is filed straight into Persona, and the
            # turn's episode is its identity there: the tombstone key if it is
            # forgotten, and what stops consolidation re-extracting it.
            tool_input = {**tool_input, "episode_id": ctx.episode_id}
        # Authorisation already happened in _gate. The dispatcher just runs it.
        result = _DISPATCHER.dispatch_reactive(name, tool_input)
        if name == "memory" and tool_input.get("action") == "save":
            tool_input = {**tool_input, **_memory_outcome(result)}
        # Recorded after the call, so a tool that raises is not reported as run -
        # and with the augmented tool_input, so the Action carries the origin the
        # tool actually used rather than the one the model supplied.
        #
        # A DEVICE_TOOLS call that rejected its own input is recorded as not run:
        # its Action is an instruction to the phone, which skips ran=false, and
        # the Audit tab and reinforcement should not count a call that did
        # nothing. Other tools keep ran=True whatever they returned.
        ran = not (
            name in DEVICE_TOOLS
            and isinstance(result, dict)
            and result.get("success") is False
        )
        _record_action(name, tool_input, ctx, ran=ran)
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
    the Google Geocoding API. Same MAPS_API_KEY as tools/functions/navigation.py."""
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
            address = data["results"][0]["formatted_address"]
            # "near", not "at" - this is a reverse-geocoded GPS fix, which even at
            # fine accuracy snaps to the nearest known address point rather than
            # confirming the user is standing at that exact spot. Every other local
            # tool hands the model a ready-made "spoken" line for this reason (see
            # navigation.py/memory_tool.py); this one didn't, so the model was free
            # to phrase it as flatly certain ("You're at ...").
            return {
                "success": True,
                "address": address,
                "spoken": f"You're near {address}.",
            }
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

    # See TurnContext.scheduled_departure - carried through so Android can
    # schedule a precise alarm even on a turn where speech stayed empty (an
    # ambient check that isn't urgent yet, but now knows exactly when it will be).
    scheduled_departure: dict[str, Any] | None = None


class NeedMoreResult(BaseModel):
    """
    Returned instead of IntentResult when the model called a CLIENT_TOOLS tool.
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
    # get_reminders only - whether the phone should include completed ones.
    include_done: bool | None = None


_PENDING_SESSIONS: dict[str, dict[str, Any]] = {}

# A paused turn the phone never came back to (app killed, network gone) would
# otherwise sit in memory until the instance restarts.
_PENDING_SESSION_TTL = timedelta(minutes=10)


def _prune_pending_sessions(now: datetime) -> None:
    for session_id in [k for k, v in _PENDING_SESSIONS.items() if v["expires_at"] < now]:
        _PENDING_SESSIONS.pop(session_id, None)

# Carries the *actual* message thread (not a recap of it) across one voice
# turn when the previous turn ended by asking the user a question. Without
# this, the only trace of "what did I ask" a later turn gets is its own
# spoken outcome text sitting in recent_episodes - which is why a bare "yes"
# could take two tries to land: the model had to reparse its own prior
# sentence instead of just seeing the question as a live turn in context.
# One slot per user. It used to be a single
# global, from when there was one user: with accounts, A's "yes" would have
# continued B's thread - B's calendar and all - in A's reply.
_PENDING_CONFIRMATION: dict[str, dict[str, Any]] = {}

# How long a dangling question stays answerable. Long enough for a real
# "yes" a few seconds later, short enough that a stale question from minutes
# ago doesn't hijack an unrelated new one.
_PENDING_CONFIRMATION_TTL = timedelta(minutes=3)

# Defensive cap on how many turns a confirmation thread may chain before it's
# abandoned and started fresh - guards against an unbroken run of clarifying
# questions growing the prompt without bound.
_PENDING_CONFIRMATION_MAX_MESSAGES = 12


def _stash_pending_confirmation(user_id: UUID | str | None, messages: list[dict[str, Any]]) -> None:
    """Called when a voice turn ends on a question - keeps the live thread
    so this user's next voice turn can continue it instead of starting over."""
    key = str(user_id)
    if len(messages) > _PENDING_CONFIRMATION_MAX_MESSAGES:
        _PENDING_CONFIRMATION.pop(key, None)
        return
    now = datetime.now(timezone.utc)
    # Drop other users' stale threads while here, so the dict can't grow
    # with everyone who ever left a question hanging.
    for stale in [k for k, v in _PENDING_CONFIRMATION.items() if v["expires_at"] < now]:
        _PENDING_CONFIRMATION.pop(stale, None)
    _PENDING_CONFIRMATION[key] = {
        "messages": messages,
        "expires_at": now + _PENDING_CONFIRMATION_TTL,
    }


def _pop_pending_confirmation(user_id: UUID | str | None) -> list[dict[str, Any]] | None:
    """Consumes this user's pending thread, if any and still fresh. Popped
    rather than peeked so a resolved or abandoned turn can't be answered twice."""
    pending = _PENDING_CONFIRMATION.pop(str(user_id), None)
    if pending is None:
        return None
    if datetime.now(timezone.utc) > pending["expires_at"]:
        return None
    return pending["messages"]


def _clear_pending_confirmation(user_id: UUID | str | None) -> None:
    """Called when a voice turn resolves without leaving a question open -
    any earlier dangling question is now moot."""
    _PENDING_CONFIRMATION.pop(str(user_id), None)


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

def _recent_episodes(user_id: UUID | str, event: Event) -> list[dict[str, Any]]:
    """
    This user's last RECENT_EPISODES Episodes of this event's type, oldest first, as
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
        rows = memory.recent(user_id, event.type, RECENT_EPISODES + 1)
    except Exception as e:
        print(f"[memory] read skipped: {e}")
        return []

    current_id = str(event.id)
    past = [r for r in rows if (r.get("event") or {}).get("id") != current_id]
    print(f"[memory] {len(past)} past {event.type!r} episodes, using last {RECENT_EPISODES}")
    return [_for_model_episode(r) for r in past[-RECENT_EPISODES:]]


def _for_model_episode(row: dict[str, Any]) -> dict[str, Any]:
    stored_state = dict(row.get("user_state") or {})
    # main.py never stores these, but older rows or a hand-written one might.
    # A stale reminder list must never compete with the live one this turn.
    stored_state.pop("reminders", None)
    stored_state.pop("reminders_pending_total", None)
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
            a.model_copy(
                update={"error": None, "authority": None, "gain": None, "reason": None}
            ).for_wire()
            for a in Action.from_episode(action_column)
        ],
    }


def _relevant_persona(user_id: UUID | str, event: Event) -> list[dict[str, Any]]:
    """
    Long-term facts about this user, retrieved by meaning for this event.

    The other half of what recent_episodes does. recent_episodes is the last
    few raw episodes OF THIS EVENT TYPE - short-term, narrow, and it scrolls:
    the five bagel-shop trips fall out of it after five more navigations, and
    with them any way to answer "what's my usual?". Persona is where that habit
    lives once app/store/consolidation has counted it, and a vector search finds
    it however the user phrases the question.

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
        matches = persona.search(user_id, persona.PersonaQuery(
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
            # Stated vs worked-out: how much to lean on it. Conflicts between
            # them are already settled in the store (persona.remember).
            "source": (m.fact.metadata or {}).get("source", "stated"),
            "support": (m.fact.metadata or {}).get("support"),
            "similarity": round(m.similarity, 3),
            "metadata": m.fact.metadata or {},
        }
        for m in matches
    ]


def _standing_instructions(user_id: UUID | str) -> list[str]:
    """What the user has told Nova about how to behave (persona's "Nova" topic).
    Listed here even when the search above also found one, so the model always
    sees it marked as an instruction rather than just a fact. Non-fatal."""
    try:
        standing = persona.standing_instructions(user_id)
    except Exception as e:
        print(f"[persona] standing instructions skipped: {e}")
        return []
    texts = [f.text for f in standing]
    if texts:
        print(f"[persona] {len(texts)} standing instruction(s)")
    return texts


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
    user_id: UUID | str, user_state: UserState, event: Event, episode_id: str | None = None
) -> IntentResult | NeedMoreResult:
    """One turn, for one user: their history, Persona, dials and pending
    question, and nobody else's. Tools that need the user read it from
    core/request_user.py, which this sets for the turn's duration - so a caller
    outside a request (a test, a script) gets the same scoping."""
    with as_user(user_id):
        return _run(user_id, user_state, event, episode_id)


def _run(
    user_id: UUID | str, user_state: UserState, event: Event, episode_id: str | None
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

    facts = _relevant_persona(user_id, event)

    # event.timestamp is always UTC; local_time is that same instant converted
    # to the user's own clock.
    local_time = event.timestamp + timedelta(minutes=user_state.utc_offset_minutes)

    # The user's onboarding answers (store/profile.py), as this moment's
    # estimator inputs. None before onboarding, which the Observer reads as
    # "behave as before".
    saved_profile = profile.for_turn(user_id)
    priors = saved_profile.answers.to_features(local_time) if saved_profile else None

    # --- the control loop, before the model runs -----------------------------
    # In this order, and all of it deterministic. By the time the model is called,
    # what may happen this turn is already settled; the model's job is to choose
    # parameters and words.
    observation = observe(event, user_state, trends=trends_from_facts(facts), priors=priors)
    # The batcher used to keep its own copy of calendar_ctx/dnd to derive this -
    # now it just gets told, same source of truth as everything else this turn.
    set_batcher_mode(user_id, observation.mode.value)
    command = classify(event)
    gains, _ = _gains_for(user_id)
    turn = ProportionalController(gains).open_turn(observation, command)
    authorised = turn.authorised()
    print(f"[control] predicted={observation.predicted.value} "
          f"confidence={observation.prediction_confidence:.2f} "
          f"command={command is not None} authorised={authorised!r}")

    user_state_dump = user_state.model_dump(mode="json")
    _localize_calendar_events(user_state_dump.get("current_events", []), user_state.utc_offset_minutes)
    _localize_calendar_events(user_state_dump.get("upcoming_events", []), user_state.utc_offset_minutes)

    payload = {
        "event": _strip_utc_fields(event.model_dump(mode="json")),
        "user_state": _strip_utc_fields(user_state_dump),
        "local_time": local_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "recent_episodes": _recent_episodes(user_id, event),
        # Short-term above, long-term here: the detail of the last few similar
        # events, plus the durable facts consolidation has distilled out of all
        # of them. "Parked on level 3" only ever appears in the first.
        "persona": _for_model(facts),
    }
    # How the user wants Nova to behave, on every turn - see the system prompt.
    standing = _standing_instructions(user_id)
    if standing:
        payload["standing_instructions"] = standing
    # What they asked to be called in onboarding. Left out rather than null when
    # unset, so a turn without it reads exactly as before.
    if saved_profile and saved_profile.display_name:
        payload["user_name"] = saved_profile.display_name

    is_voice = event.type == "voice"
    # Only voice turns can be "yes"/"no" answers to a prior spoken question,
    # so only voice turns consume the pending thread - an ambient event
    # arriving in between (location update, notification, ...) must not
    # steal or clear it.
    carried = _pop_pending_confirmation(user_id) if is_voice else None
    new_turn: dict[str, Any] = {"role": "user", "content": json.dumps(payload)}
    messages: list[dict[str, Any]] = [*carried, new_turn] if carried else [new_turn]
    if carried:
        print("[loop] continuing pending confirmation thread")

    ctx = TurnContext(
        turn=turn,
        user_id=user_id,
        location_ctx=user_state.location_ctx,
        # The phone's Settings value, else the onboarding answer.
        preferred_travel_mode=user_state.preferred_travel_mode
        or (saved_profile.answers.travel_mode if saved_profile else None),
        utc_offset_minutes=user_state.utc_offset_minutes,
        episode_id=episode_id,
        canvas=canvas_store.is_connected(user_id),
        known_reminder_ids=(
            {r.id for r in user_state.reminders}
            if user_state.reminders_pending_total is not None else None
        ),
    )
    verbatim = note_body(event)
    if verbatim is not None and "memory" in authorised:
        return _save_verbatim_note(verbatim, event.id, ctx)
    return _run_loop(messages, MAX_ITERATIONS, event.id, ctx, is_voice=is_voice)


def _save_verbatim_note(text: str, event_id: UUID, ctx: TurnContext) -> IntentResult:
    """ "note ..." saved word for word, without the model (control/commands.py
    note_body). Through the memory tool all the same, so it is gated, recorded
    as an Action and reinforced like any other save - just not rephrased."""
    # A note answers nothing, so a question left pending before it is dropped.
    _clear_pending_confirmation(ctx.user_id)
    result = _run_local_tool("memory", {"action": "save", "text": text}, ctx)
    speech = result.get("spoken", "") if isinstance(result, dict) else ""
    print(f"[loop] verbatim note - final speech={speech!r} actions={ctx.ran!r}")
    return IntentResult(
        event_id=event_id, speech=speech, actions=ctx.for_wire(),
        episode_id=ctx.episode_id,
    )


def resume(user_id: UUID | str, session_id: str, tool_result: Any) -> IntentResult | NeedMoreResult:
    """Resumes a conversation paused on a CLIENT_TOOLS call, feeding the
    client-supplied result back in as that tool's result. Raises KeyError if
    session_id is unknown (already resumed, expired, or the process restarted)
    - or isn't this user's: someone else's session looks exactly like
    one that doesn't exist, and stays parked for its owner."""
    pending = _PENDING_SESSIONS.get(session_id)
    if (pending is None
            or str(pending["user_id"]) != str(user_id)
            or pending["expires_at"] < datetime.now(timezone.utc)):
        raise KeyError(f"unknown or expired session_id: {session_id!r}")
    del _PENDING_SESSIONS[session_id]
    with as_user(user_id):
        return _resume(pending, tool_result)


def _resume(pending: dict[str, Any], tool_result: Any) -> IntentResult | NeedMoreResult:
    utc_offset_minutes = pending.get("utc_offset_minutes", 0)
    if isinstance(tool_result, dict) and isinstance(tool_result.get("events"), list):
        _localize_calendar_events(tool_result["events"], utc_offset_minutes)

    # A get_reminders result widens what update_reminder may name this turn.
    ctx: TurnContext = pending["ctx"]
    if isinstance(tool_result, dict) and isinstance(tool_result.get("reminders"), list):
        found = {str(r["id"]) for r in tool_result["reminders"] if isinstance(r, dict) and r.get("id")}
        ctx.known_reminder_ids = (ctx.known_reminder_ids or set()) | found

    messages: list[dict[str, Any]] = pending["messages"]
    # Every call in the paused assistant message is answered before the model
    # runs again: the ones that ran before the hop, then the phone's.
    messages.extend(pending.get("pending_tool_results", []))
    messages.append(_tool_message(pending["tool_call_id"], tool_result))
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


# Room for the answer once thinking has been cut short (see _force_answer).
FORCED_ANSWER_TOKENS = 1024
_WRAP_UP = "\n\nI've thought about this enough - time to act on it and give my answer."
STUCK_SPEECH = "Sorry, I got tangled up working that out. Could you ask me again?"


def _force_answer(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]], reasoning: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    """The model thought until max_tokens and never answered. Hand its thinking
    back, closed off with a line saying it's time to answer, and let it carry on
    from there - budget forcing. It then answers, or calls the tool it was
    working towards, in a second or two instead of the turn ending blank.

    Needed more as standing instructions pile up: "reply in rhymes, in Pig
    Latin, every word starting with a" is a puzzle the model will happily think
    about for longer than any budget.

    The </think> is in the prompt rather than the output, so the server's
    reasoning parser files the whole continuation as reasoning - hence
    content-or-reasoning below. Returns what _assistant_turn does, plus the
    finish reason to carry on the loop with.
    """
    print(f"[loop] thinking ran out after {len(reasoning)} chars - forcing an answer")
    try:
        response = llm.client(LOOP_TIMEOUT_S, 1).chat.completions.create(
            model=llm.MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT}, *messages,
                {"role": "assistant", "content": f"<think>\n{reasoning.strip()}{_WRAP_UP}\n</think>\n\n"},
            ],
            tools=tools,
            tool_choice="auto",
            max_tokens=FORCED_ANSWER_TOKENS,
            temperature=0.3,
            extra_body={**llm.THINKING, "continue_final_message": True, "add_generation_prompt": False},
        )
    except Exception as e:
        print(f"[loop] forced answer failed: {e}")
        return {"role": "assistant", "content": None}, [], "length"
    choice = response.choices[0]
    text = choice.message.content or llm.reasoning_of(choice.message)
    assistant, calls = _assistant_turn(SimpleNamespace(content=text, tool_calls=choice.message.tool_calls))
    finish = choice.finish_reason
    # Cut off again but with something to say: say it.
    if finish == "length" and assistant["content"] and not calls:
        finish = "stop"
    print(f"[loop] forced answer finish_reason={finish!r} "
          f"tool_calls={[c['function']['name'] for c in calls]!r} speech={assistant['content']!r}")
    return assistant, calls, finish


def _tool_message(call_id: str, result: Any) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, default=str)}


def _assistant_turn(message: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The model's reply as a plain message dict for the thread, and its tool
    calls. Plain dicts, not SDK objects, so a parked or stashed thread is
    ordinary data. Tool calls a server left in the text as <tool_call> markup
    are lifted out into real calls."""
    text = llm.strip_thinking(message.content)
    calls = [
        {"id": c.id, "type": "function",
         "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
        for c in (message.tool_calls or [])
    ]
    if not calls:
        calls = llm.tool_calls_in_text(text)
    text = llm.without_tool_call_text(text)
    turn: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        turn["tool_calls"] = calls
    return turn, calls


def _arguments(call: dict[str, Any]) -> dict[str, Any] | None:
    """A tool call's arguments, or None if they aren't a JSON object."""
    raw = call["function"].get("arguments") or "{}"
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


_BAD_ARGUMENTS = {"success": False, "error": (
    "The arguments for that call were not a valid JSON object. Call it again "
    "with valid JSON arguments.")}


def _run_loop(
    messages: list[dict[str, Any]],
    iterations_left: int,
    event_id: UUID,
    ctx: TurnContext,
    is_voice: bool = False,
) -> IntentResult | NeedMoreResult:
    # Read off the Turn rather than passed in.
    tools = _build_tools(ctx.turn.authorised(), canvas=ctx.canvas)

    # The last line a tool offered for the user to hear. Spoken if the model
    # ends the turn saying nothing - after a save it sometimes returns no text
    # at all, and a request met with silence reads as not having worked.
    tool_spoken = ""

    # iterate until an appropriate answer is reached
    for _ in range(iterations_left):
        # call model
        print(f"[loop] tools={[t['function']['name'] for t in tools]!r}")
        response = llm.client(LOOP_TIMEOUT_S, 1).chat.completions.create(
            model=llm.MODEL,
            # The system prompt goes first and never changes, so the model
            # server's prefix cache covers it (and the tools) across turns.
            # It isn't kept in `messages`, so parked and stashed threads don't
            # carry a copy of it.
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
            tools=tools,
            tool_choice="auto",
            max_tokens=1024 + llm.THINKING_TOKENS,
            temperature=0.3,
            # Thinking on: the server hands it back apart from the answer
            # (see llm.THINKING). It is not kept in the thread.
            extra_body=llm.THINKING,
        )
        choice = response.choices[0]
        assistant, calls = _assistant_turn(choice.message)
        finish = choice.finish_reason
        usage = getattr(response, "usage", None)
        cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
        print(f"[loop] finish_reason={choice.finish_reason!r} "
              f"tool_calls={[c['function']['name'] for c in calls]!r} "
              f"prompt={getattr(usage, 'prompt_tokens', None)} cached={cached} "
              f"completion={getattr(usage, 'completion_tokens', None)}")

        # Thinking used the whole budget and nothing came out: make it stop
        # thinking and answer, rather than end the turn in silence.
        if finish == "length" and not calls and not assistant["content"]:
            assistant, calls, finish = _force_answer(messages, tools, llm.reasoning_of(choice.message))

        # if a tool is called. Checked before finish_reason: some servers
        # report "stop" on a reply that carries tool calls.
        if calls:
            messages.append(assistant)

            # A client tool goes through the same check as everything else before
            # the backend pauses and calls out to the phone - it is a registered
            # Function tool with a dial like the others. If it was not authorised,
            # fall through and let the loop below hand back the refusal instead
            # of hopping to the device.
            client_call = next(
                (c for c in calls
                 if c["function"]["name"] in CLIENT_TOOLS and _arguments(c) is not None),
                None,
            )
            client_authorised = (
                client_call is not None and _gate(client_call["function"]["name"], ctx) is None
            )

            # Every OTHER call in this same reply runs now regardless of the
            # client hop below - every tool_call id in an assistant message has
            # to be answered before the model runs again, so a reply that called
            # e.g. both get_calendar_range and navigation_departure_time can't
            # just answer the first and leave the second dangling until
            # resume(). Its result is carried in _PENDING_SESSIONS and sent with
            # the device's answer there instead.
            tool_results = []
            for call in calls:
                if call is client_call and client_authorised:
                    continue
                name = call["function"]["name"]
                tool_input = _arguments(call)
                if tool_input is None:
                    tool_results.append(_tool_message(call["id"], _BAD_ARGUMENTS))
                    continue
                blocked = _gate(name, ctx)
                if blocked is not None:
                    # Refused calls are recorded here; allowed ones are
                    # recorded by _run_local_tool once they return.
                    _record_action(name, tool_input, ctx, ran=False)
                result = blocked if blocked is not None else _run_local_tool(
                    name, tool_input, ctx
                )
                if (name == "navigation_departure_time"
                        and isinstance(result, dict)
                        and "leave_in_minutes" in result):
                    ctx.scheduled_departure = {
                        "destination": result.get("destination"),
                        "mode": result.get("mode"),
                        "leave_in_minutes": result["leave_in_minutes"],
                        # The commitment's own countdown and title, as passed into the
                        # tool call (see SYSTEM_PROMPT's WHEN TO LEAVE) - carried through
                        # so Android can fill "you have X in N minutes" without asking the
                        # model to phrase it. Both None for a destination with no calendar
                        # anchor.
                        "minutes_until_start": tool_input.get("minutes_until_start"),
                        "event_title": tool_input.get("event_title"),
                    }
                if isinstance(result, dict) and result.get("spoken"):
                    tool_spoken = str(result["spoken"])
                tool_results.append(_tool_message(call["id"], result))

            if client_authorised:
                client_input = _arguments(client_call) or {}
                client_name = client_call["function"]["name"]
                _record_action(client_name, client_input, ctx, ran=True)
                session_id = str(uuid.uuid4())
                now = datetime.now(timezone.utc)
                _prune_pending_sessions(now)
                _PENDING_SESSIONS[session_id] = {
                    # resume() hands this back only to the same user.
                    "user_id": ctx.user_id,
                    "expires_at": now + _PENDING_SESSION_TTL,
                    "messages": messages,
                    "tool_call_id": client_call["id"],
                    # Any other call from this same reply already ran above and
                    # has its tool message sitting here - resume() sends these
                    # along with the device's eventual answer (see above).
                    "pending_tool_results": tool_results,
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
                    request_type=client_name,
                    from_time=client_input.get("from_time", ""),
                    to_time=client_input.get("to_time", ""),
                    include_done=(
                        bool(client_input.get("include_done"))
                        if client_name == "get_reminders" else None
                    ),
                )

            messages.extend(tool_results)
            continue

        # finished reasoning
        if finish in ("stop", "eos", None):
            speech = assistant["content"] or ""
            if not speech:
                print(f"[loop] empty speech - raw content: {choice.message.content!r}")
                speech = tool_spoken
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
                        ctx.user_id,
                        [*messages, {"role": "assistant", "content": speech}],
                    )
                else:
                    _clear_pending_confirmation(ctx.user_id)
            return IntentResult(
                event_id=event_id, speech=speech, actions=ctx.for_wire(),
                episode_id=ctx.episode_id, confirmation=confirmation,
                scheduled_departure=ctx.scheduled_departure,
            )

        # unexpected finish_reason (length, content_filter, ...)
        print(f"[loop] breaking on unexpected finish_reason={finish!r}")
        break

    # Someone who spoke gets an answer, even if it's that this one went wrong;
    # an ambient event stays silent, as it always may.
    speech = (tool_spoken or STUCK_SPEECH) if is_voice else ""
    print(f"[loop] exited loop with no end_turn - speech={speech!r}")
    return IntentResult(
        event_id=event_id, speech=speech, actions=ctx.for_wire(),
        episode_id=ctx.episode_id, scheduled_departure=ctx.scheduled_departure,
    )
