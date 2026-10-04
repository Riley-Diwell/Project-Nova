"""An answer to NOVA's own question keeps the authority of the request it asked
for.

"create a reminder and a note the code is 4417" -> "when do you want me to ping
you?" -> "tomorrow 10:00 am". The answer is no command by classify()'s grammar,
so the turn used to be judged by gain alone, set_reminder was left out of the
tools, and NOVA said it had no way to set a reminder.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import intent_surface
from app.control.commands import Command, classify
from app.control.controller import ProportionalController
from app.control.observer import Observation
from app.schemas.event import VoiceEvent

USER = uuid4()
ASKED = "create a remindeer and a note the code is 4417"
QUESTION = "I've saved the note. For the reminder, when do you want me to ping you about it?"


def voice(text):
    return VoiceEvent(id=uuid4(), timestamp=datetime.now(timezone.utc), text=text)


class _Reached(Exception):
    """Raised where the Controller opens the turn, carrying the command it got."""


@pytest.fixture
def opened(monkeypatch):
    """Runs _run up to the Controller and returns the command it was handed."""
    monkeypatch.setattr(intent_surface, "MOCK_LLM", False)
    monkeypatch.setattr(intent_surface, "_PENDING_CONFIRMATION", {})
    monkeypatch.setattr(intent_surface, "_PENDING_REMINDER_OFFER", {})
    monkeypatch.setattr(intent_surface, "_relevant_persona", lambda user_id, event: [])
    monkeypatch.setattr(intent_surface.profile, "for_turn", lambda user_id: None)
    monkeypatch.setattr(intent_surface, "observe", lambda *a, **kw: Observation())
    monkeypatch.setattr(intent_surface, "set_batcher_mode", lambda user_id, mode: None)
    monkeypatch.setattr(intent_surface, "_gains_for", lambda user_id: (intent_surface._REGISTRY, None))

    class Controller:
        def __init__(self, gains):
            pass

        def open_turn(self, observation, command):
            raise _Reached(command)

    monkeypatch.setattr(intent_surface, "ProportionalController", Controller)

    def run(text):
        with pytest.raises(_Reached) as got:
            intent_surface._run(USER, SimpleNamespace(utc_offset_minutes=600), voice(text), None)
        return got.value.args[0]

    return run


def test_the_answer_alone_is_not_a_command():
    assert classify(voice("tomorrow 10:00 am")) is None


def test_an_answer_to_a_commanded_question_is_commanded(opened):
    intent_surface._stash_pending_confirmation(
        USER, [{"role": "assistant", "content": QUESTION}], command=Command(text=ASKED))
    assert opened("tomorrow 10:00 am") == Command(text=ASKED)


def test_an_answer_to_an_unasked_question_stays_uncommanded(opened):
    # NOVA asked off its own bat (an ambient nudge): the answer gets no more
    # authority than the question had.
    intent_surface._stash_pending_confirmation(USER, [{"role": "assistant", "content": QUESTION}])
    assert opened("tomorrow 10:00 am") is None


def test_the_answers_own_command_wins(opened):
    intent_surface._stash_pending_confirmation(
        USER, [{"role": "assistant", "content": QUESTION}], command=Command(text=ASKED))
    assert opened("make it 10am tomorrow") == Command(text="make it 10am tomorrow")


def test_no_thread_no_command(opened):
    assert opened("tomorrow 10:00 am") is None


def test_a_commanded_question_stashes_its_command(monkeypatch):
    monkeypatch.setattr(intent_surface, "_PENDING_CONFIRMATION", {})
    command = Command(text=ASKED)
    turn = ProportionalController(intent_surface._REGISTRY).open_turn(Observation(), command)
    ctx = intent_surface.TurnContext(turn=turn, user_id=USER, command=command)
    reply = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=QUESTION, tool_calls=None),
                                 finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, prompt_tokens_details=None),
    )
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: reply)))
    monkeypatch.setattr(intent_surface.llm, "client", lambda *a, **kw: fake)

    intent_surface._run_loop([{"role": "user", "content": "{}"}], 3, uuid4(), ctx, is_voice=True)
    assert intent_surface._pop_pending_turn(USER)["command"] == command
    assert "set_reminder" in turn.authorised()
