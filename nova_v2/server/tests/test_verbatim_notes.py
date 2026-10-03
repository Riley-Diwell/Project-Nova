""""note ..." is saved word for word, and a save is never met with silence.

The model used to handle "note ...": it paraphrased the user's words into a
one-line description before saving, and after the save it sometimes ended the
turn with no text, so NOVA said nothing at all.
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import intent_surface
from app.control.commands import Command, classify, note_body, offer_reply
from app.control.cues import note_wants_reminder
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
    assert result.speech == "Noted."
    assert result.confirmation is None
    assert ctx.actions[-1].tool == "memory" and ctx.actions[-1].ran is True
    # Where it landed, so the phone's chip can say "Saved to Notes" and open it.
    assert ctx.actions[-1].input["saved_as"] == "note"
    assert ctx.actions[-1].input["note_id"] == ctx.saved[0].id


def _reply(finish_reason, content=None, tool_calls=None):
    """One chat-completions response, as intent_surface._run_loop reads it."""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content, tool_calls=tool_calls),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, prompt_tokens_details=None),
    )


def test_a_silent_end_turn_speaks_the_tool_line(ctx, monkeypatch):
    save_call = SimpleNamespace(id="t1", function=SimpleNamespace(
        name="memory", arguments=json.dumps({"action": "save", "text": "parked on level 3"})))
    replies = iter([_reply("tool_calls", tool_calls=[save_call]), _reply("stop", content="")])
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: next(replies))))
    monkeypatch.setattr(intent_surface.llm, "client", lambda *a, **kw: fake)
    result = intent_surface._run_loop([{"role": "user", "content": "{}"}], 3, uuid4(), ctx)
    assert result.speech == "Noted."


# --- a note with a time in it offers a reminder --------------------------------

@pytest.mark.parametrize("body", [
    "submit the form by 5",
    "call Mum tonight",
    "email Dr Chen tomorrow",
    "take the pasta off in 20 minutes",
    "ask the tutor after class",
])
def test_timed_notes_want_a_reminder(body):
    assert note_wants_reminder(body)


@pytest.mark.parametrize("body", [
    "gate code 4471",
    "ask tutor about Q3",
    # A poem mentioning tonight is a record, not a to-do.
    ("in the light of the moon a little egg lay on a leaf and one sunday morning "
     "the warm sun came up and pop out of the egg came a tiny and very hungry "
     "caterpillar who ate tonight"),
])
def test_untimed_or_long_notes_do_not(body):
    assert not note_wants_reminder(body)


@pytest.mark.parametrize("text, answer", [
    ("yes", True), ("Yes please", True), ("yeah", True), ("sure, thanks", True),
    ("okay", True), ("Nova yes", True),
    ("no", False), ("No thanks", False), ("nah", False), ("no, just the note", False),
    ("yes but make it 6", None), ("what's the weather", None), ("", None),
])
def test_offer_reply(text, answer):
    assert offer_reply(voice(text)) is answer


@pytest.fixture
def offers(monkeypatch):
    monkeypatch.setattr(intent_surface, "_PENDING_REMINDER_OFFER", {})
    return intent_surface._PENDING_REMINDER_OFFER


def test_a_timed_note_is_saved_and_offers_a_reminder(ctx, offers):
    result = intent_surface._save_verbatim_note("submit the form by 5", uuid4(), ctx)
    assert [n.text for n in ctx.saved] == ["submit the form by 5"]
    assert result.speech == intent_surface.REMINDER_OFFER_SPEECH
    assert result.confirmation == "yes_no"
    assert intent_surface._pop_reminder_offer(USER) == "submit the form by 5"


def test_no_offer_when_reminders_are_not_authorised(ctx, offers, monkeypatch):
    monkeypatch.setattr(ctx.turn, "authorised", lambda: ["memory"])
    result = intent_surface._save_verbatim_note("submit the form by 5", uuid4(), ctx)
    assert result.speech == "Noted."
    assert offers == {}


class _Reached(Exception):
    """Raised where the normal turn would begin, carrying the Event it got."""


def test_yes_to_the_offer_becomes_a_reminder_command(offers, monkeypatch):
    monkeypatch.setattr(intent_surface, "MOCK_LLM", False)

    def reached(user_id, event):
        raise _Reached(event)

    monkeypatch.setattr(intent_surface, "_relevant_persona", reached)
    intent_surface._stash_reminder_offer(USER, "submit the form by 5")
    with pytest.raises(_Reached) as got:
        intent_surface._run(USER, SimpleNamespace(), voice("yes please"), None)
    event = got.value.args[0]
    assert event.text == "Remind me about this: submit the form by 5"
    assert classify(event) is not None
    assert offers == {}


def test_no_to_the_offer_keeps_just_the_note(offers, monkeypatch):
    monkeypatch.setattr(intent_surface, "MOCK_LLM", False)
    intent_surface._stash_reminder_offer(USER, "submit the form by 5")
    result = intent_surface._run(USER, SimpleNamespace(), voice("no thanks"), None)
    assert result.speech == intent_surface.REMINDER_DECLINED_SPEECH
    assert result.actions == []
    assert offers == {}


def test_anything_else_drops_the_offer(offers, monkeypatch):
    monkeypatch.setattr(intent_surface, "MOCK_LLM", False)

    def reached(user_id, event):
        raise _Reached(event)

    monkeypatch.setattr(intent_surface, "_relevant_persona", reached)
    intent_surface._stash_reminder_offer(USER, "submit the form by 5")
    with pytest.raises(_Reached) as got:
        intent_surface._run(USER, SimpleNamespace(), voice("what's the weather"), None)
    assert got.value.args[0].text == "what's the weather"
    assert offers == {}


# --- recall keeps what Nova knows apart from notes ------------------------------

def test_recall_speaks_known_facts_and_notes_separately():
    fact = {"tier": "known_fact", "text": "Allergic to peanuts"}
    note = {"tier": "note", "note_id": "n1", "title": "Satay recipe", "snippet": "..."}
    assert memory_tool._recalled([fact], True)["spoken"] == "What I know: Allergic to peanuts"
    assert memory_tool._recalled([note], True)["spoken"] == "One note: Satay recipe"
    assert memory_tool._recalled([fact, note], True)["spoken"] == (
        "What I know: Allergic to peanuts. One note: Satay recipe")
    assert memory_tool._recalled([], True)["spoken"] == "I haven't got anything on that."
