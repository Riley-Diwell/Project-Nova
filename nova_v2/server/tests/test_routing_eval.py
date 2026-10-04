"""Routing eval: does "remind me ..." reach set_reminder, and nothing else?

Notes, memory and reminders were separated (f20d4d8), and timers, alarms and
the calendar all sit next to set_reminder in the tool list. Each phrase is
checked at both places a turn is routed:

1. Deterministic, always run. classify() must call it a command (so every
   Function tool is authorised, gain or not), note_body() must not take it
   down the verbatim note path, which never reaches the model, and the
   expected tool must be in what the Controller authorised.
2. The model, only with NOVA_LIVE_EVAL=1 and a reachable LLM_BASE_URL (plus
   LLM_API_KEY, e.g. the server's LLM_SHARE_KEY over Tailscale). The same
   request _run_loop makes on a turn's first pass - system prompt, tool list,
   payload shape, sampling - with no history or Persona, and the first
   Function tool the model calls is compared with the expected one.

    LLM_BASE_URL=https://<host>.<tailnet>.ts.net/v1 LLM_API_KEY=... \
    NOVA_LIVE_EVAL=1 .venv/Scripts/python -m pytest tests/test_routing_eval.py -rA

The controls go the other way: a timer, an alarm, a calendar entry, a note and
a memory, none of which should become a reminder.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app import intent_surface
from app.control.commands import classify, note_body
from app.control.controller import ProportionalController
from app.control.observer import observe
from app.core import llm
from app.schemas.event import VoiceEvent
from app.schemas.user_state import UserState
from app.tools.core.catalogue import build_registry

LIVE = os.environ.get("NOVA_LIVE_EVAL", "").strip().lower() in ("1", "true", "yes")

# Called before or alongside a Function tool without being the routing choice.
CONTEXT_TOOLS = {"web_search", "get_current_address", "canvas"}

# A sentinel for "never reaches the model": note_body() takes these first.
VERBATIM_NOTE = "verbatim note"

REMINDERS = [
    "remind me to call mum at 6",
    "remind me to take the bins out tonight",
    "remind me in 20 minutes to check the oven",
    "remind me tomorrow morning to email my tutor",
    "remind me to buy milk when I get to Woolworths",
    "remind me about the assignment on Friday",
    "Nova, remind me at 3pm to submit the form",
    "can you remind me to drink water in an hour",
    "remind me to grab my charger before I leave home",
    "remind me on Monday to pay rent",
    "don't let me forget to text Sam tonight",
    "remind me in 5 minutes to move the washing",
    "set a reminder for 9am to ring the dentist",
    "remind me that the library books are due Thursday",
    "remind me to stand up in half an hour",
    "please remind me to pick up the parcel from the post office",
    "hey nova remind me when I get home to water the plants",
    "remind me to bring my laptop to the COMP2100 lecture",
    "remind me at 7 tomorrow to go for a run",
    "remind me to call the bank at lunchtime",
]

CONTROLS = [
    ("set a timer for 10 minutes", "set_timer"),
    ("set an alarm for 6:30 tomorrow", "set_alarm"),
    ("add the dentist to my calendar on Friday at 3", "add_calendar_event"),
    ("note that the draft is due Friday", VERBATIM_NOTE),
    ("remember that my locker code is 4412", "memory"),
]

CASES = [(text, "set_reminder") for text in REMINDERS] + CONTROLS

# Wednesday 2026-09-23, 12:00 in Canberra.
NOW = datetime(2026, 9, 23, 2, 0, tzinfo=timezone.utc)


def _state() -> UserState:
    return UserState(calendar_ctx="free", activity="still", location_ctx="-35.28,149.13",
                     utc_offset_minutes=600)


def _event(text: str) -> VoiceEvent:
    return VoiceEvent(id=uuid4(), timestamp=NOW, text=text)


def _authorised(event: VoiceEvent, state: UserState) -> list[str]:
    # The seed registry's gains: a commanded turn authorises every Function
    # tool whatever its gain, so no user's dials change the answer.
    turn = ProportionalController(build_registry()).open_turn(observe(event, state), classify(event))
    return turn.authorised()


@pytest.mark.parametrize("text, expected", CASES)
def test_routed_before_the_model(text, expected):
    event = _event(text)
    if expected == VERBATIM_NOTE:
        assert note_body(event) is not None
        return
    assert note_body(event) is None, "taken as a verbatim note, so the model never sees it"
    assert classify(event) is not None, "not read as a command"
    assert expected in _authorised(event, _state())


def _first_choice(text: str) -> list[str]:
    """The tools the model calls on the turn's first pass, in order - the
    request _run_loop sends, built the way _run builds it."""
    event, state = _event(text), _state()
    state_dump = state.model_dump(mode="json")
    payload = {
        "event": intent_surface._strip_utc_fields(event.model_dump(mode="json")),
        "user_state": intent_surface._strip_utc_fields(state_dump),
        "local_time": (NOW + timedelta(minutes=state.utc_offset_minutes)).strftime("%Y-%m-%dT%H:%M:%S"),
        "recent_episodes": [],
        "persona": [],
    }
    response = llm.client(intent_surface.LOOP_TIMEOUT_S, 1).chat.completions.create(
        model=llm.MODEL,
        messages=[{"role": "system", "content": intent_surface.SYSTEM_PROMPT},
                  {"role": "user", "content": json.dumps(payload)}],
        tools=intent_surface._build_tools(_authorised(event, state)),
        tool_choice="auto",
        max_tokens=intent_surface.LOOP_MAX_TOKENS,
        temperature=0.3,
        extra_body=llm.THINKING,
    )
    return [c.function.name for c in (response.choices[0].message.tool_calls or [])]


@pytest.mark.skipif(not LIVE, reason="live model eval: set NOVA_LIVE_EVAL=1")
@pytest.mark.parametrize("text, expected", [c for c in CASES if c[1] != VERBATIM_NOTE])
def test_model_routes(text, expected):
    calls = _first_choice(text)
    chosen = next((name for name in calls if name not in CONTEXT_TOOLS), None)
    print(f"[eval] {text!r} -> {calls!r}")
    assert chosen == expected, f"called {calls!r}"
