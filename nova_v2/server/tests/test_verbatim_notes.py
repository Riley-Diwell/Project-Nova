""""note ..." is saved word for word, and a save is never met with silence.

The model used to handle "note ...": it paraphrased the user's words into a
one-line description before saving, and after the save it sometimes ended the
turn with no text, so NOVA said nothing at all.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import intent_surface
from app.control.commands import Command, note_body
from app.control.controller import ProportionalController
from app.control.observer import Observation
from app.schemas.event import VoiceEvent
from app.tools.functions import memory_tool

USER = uuid4()


def voice(text):
    return VoiceEvent(id=uuid4(), timestamp=datetime.now(timezone.utc), text=text)


@pytest.mark.parametrize("text, body", [
    ("note in the light of the moon a little egg", "in the light of the moon a little egg"),
    ("Hey Nova, note that the draft is due Friday", "the draft is due Friday"),
    ("Note: Buy milk", "Buy milk"),
    ("take a note ask tutor about Q3", "ask tutor about Q3"),
    ("note to self call Mum", "call Mum"),
    ("please note down gate code 4471", "gate code 4471"),
])
def test_note_prefix_keeps_the_words(text, body):
    assert note_body(voice(text)) == body


@pytest.mark.parametrize("text", [
    "note that",
    "notebook is on the desk",
    "open my notes app",
    "remember I parked on level 3",
    "what did I note about the tutor",
])
def test_not_a_note(text):
    assert note_body(voice(text)) is None


@pytest.fixture
def ctx(monkeypatch):
    saved = []

    def create(user_id, note_in):
        saved.append(note_in)
        return SimpleNamespace(id=note_in.id)

    monkeypatch.setattr(memory_tool.notes, "create", create)
    monkeypatch.setattr(memory_tool, "request_user_id", lambda: USER)
    monkeypatch.setattr(intent_surface, "_clear_pending_confirmation", lambda user_id: None)
    turn = ProportionalController(intent_surface._REGISTRY).open_turn(Observation(), Command(text="note"))
    c = intent_surface.TurnContext(turn=turn, user_id=USER, utc_offset_minutes=600)
    c.saved = saved
    return c


def test_a_note_is_saved_verbatim_and_confirmed(ctx):
    words = "in the light of the moon a little egg lay on a leaf"
    result = intent_surface._save_verbatim_note(words, uuid4(), ctx)
    assert [n.text for n in ctx.saved] == [words]
    assert result.speech == "I've saved that as a note."
    assert ctx.actions[-1].tool == "memory" and ctx.actions[-1].ran is True


def test_a_silent_end_turn_speaks_the_tool_line(ctx, monkeypatch):
    replies = iter([
        SimpleNamespace(
            stop_reason="tool_use",
            content=[SimpleNamespace(type="tool_use", id="t1", name="memory",
                                     input={"action": "save", "text": "parked on level 3"})],
            usage=SimpleNamespace(cache_creation_input_tokens=0, cache_read_input_tokens=0, input_tokens=0),
        ),
        SimpleNamespace(
            stop_reason="end_turn", content=[],
            usage=SimpleNamespace(cache_creation_input_tokens=0, cache_read_input_tokens=0, input_tokens=0),
        ),
    ])
    monkeypatch.setattr(intent_surface.client.messages, "create", lambda **kw: next(replies))
    result = intent_surface._run_loop([{"role": "user", "content": "{}"}], 3, uuid4(), ctx)
    assert result.speech == "I've saved that as a note."
