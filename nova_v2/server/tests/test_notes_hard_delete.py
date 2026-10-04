"""Deleted notes are entirely gone (docs/plans/notes-hard-delete-plan.md, phase 1):
the voice turn that saved a note, later turns that quoted it, facts read out of
those turns and anything pending in process memory all go with it, and only
content-free tombstones stay."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from notes_support import *  # noqa: F401,F403 - fixtures
from notes_support import OTHER, USER, make_note

from app import intent_surface, main
from app.control.controller import ProportionalController
from app.control.observer import Observation
from app.control.commands import Command
from app.notes_pipeline.processor import NotesPipelineProcessor
from app.notes_pipeline.summarise import MockSummariser
from app.store import memory, notes, persona
from app.store.persona import Fact

CODE = "the gate code is 4417"


def _ctx(episode_id, text="note"):
    turn = ProportionalController(intent_surface._REGISTRY).open_turn(Observation(), Command(text=text))
    return intent_surface.TurnContext(turn=turn, user_id=USER, utc_offset_minutes=600, episode_id=episode_id)


def _voice_episode(fake, text, user=USER, action=None):
    episode_id = fake.append(user, {"event_type": "voice", "event": {"type": "voice", "text": text}})
    if action is not None:
        fake.close(user, episode_id, action=action)
    return episode_id


def _reply(finish_reason, content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls),
                                 finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, prompt_tokens_details=None),
    )


def _save_by_voice(stores):
    """"note the gate code is 4417", as intent_surface handles it."""
    fake = stores["memory"]
    episode_id = _voice_episode(fake, f"note {CODE}")
    result = intent_surface._save_verbatim_note(CODE, uuid4(), _ctx(episode_id))
    main._close_episode(USER, result)
    [note] = notes.all_notes(USER)
    return note, episode_id


@pytest.fixture(autouse=True)
def _clean_pending():
    intent_surface._PENDING_REMINDER_OFFER.clear()
    intent_surface._PENDING_CONFIRMATION.clear()
    yield
    intent_surface._PENDING_REMINDER_OFFER.clear()
    intent_surface._PENDING_CONFIRMATION.clear()


# --- the voice turn that saved it -----------------------------------------------

def test_a_voice_saved_note_knows_its_turn(stores):
    note, episode_id = _save_by_voice(stores)
    assert note.origin_episode_id == episode_id


def test_deleting_it_deletes_its_turn_and_keeps_only_tombstones(stores):
    fake = stores["memory"]
    note, episode_id = _save_by_voice(stores)
    unrelated = _voice_episode(fake, "what's the weather", action={"actions": [], "speech": "Sunny."})

    notes.delete(USER, note.id)

    assert fake.get(USER, episode_id) is None
    assert fake.get(USER, unrelated) is not None
    assert CODE not in json.dumps(fake.rows)
    assert {persona.note_key(note.id), f"episode:{episode_id}"} <= persona.forgotten(USER)


def test_another_users_episodes_are_never_touched(stores):
    fake = stores["memory"]
    note, _ = _save_by_voice(stores)
    # Someone else's row that happens to contain this note's id.
    theirs = _voice_episode(fake, f"note {note.id}", user=OTHER,
                            action={"actions": [], "speech": note.id, "note_ids": [note.id]})
    notes.delete(USER, note.id)
    assert fake.get(OTHER, theirs) is not None


# --- later turns that quoted it ---------------------------------------------------

def test_a_recall_turn_records_the_notes_it_read(stores, monkeypatch):
    note = notes.create(USER, make_note(CODE))
    episode_id = _voice_episode(stores["memory"], "what's the gate code")
    ctx = _ctx(episode_id, text="what's the gate code")
    call = SimpleNamespace(id="t1", function=SimpleNamespace(
        name="memory", arguments=json.dumps({"action": "recall", "query": "gate code"})))
    replies = iter([_reply("tool_calls", tool_calls=[call]), _reply("stop", content=f"It's {CODE}.")])
    fake_llm = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: next(replies))))
    monkeypatch.setattr(intent_surface.llm, "client", lambda *a, **kw: fake_llm)

    result = intent_surface._run_loop([{"role": "user", "content": "{}"}], 3, uuid4(), ctx)
    main._close_episode(USER, result)

    [recall] = [a for a in result.actions if a["tool"] == "memory"]
    assert recall["input"]["note_ids"] == [note.id]

    notes.delete(USER, note.id)
    assert stores["memory"].get(USER, episode_id) is None
    assert f"episode:{episode_id}" in persona.forgotten(USER)


def test_the_yes_to_a_reminder_offer_goes_with_the_note(stores):
    fake = stores["memory"]
    intent_surface._stash_reminder_offer(USER, "submit the form by 5", "note-0000-1111")
    assert intent_surface._pop_reminder_offer_with_note(USER) == ("submit the form by 5", "note-0000-1111")

    # The "yes" turn: its event says only "yes", its Action holds the text.
    yes = _voice_episode(fake, "yes")
    result = intent_surface.IntentResult(
        event_id=uuid4(), speech="Okay, 5pm.", episode_id=yes, note_ids=["note-0000-1111"],
        actions=[{"tool": "set_reminder", "input": {"text": "submit the form by 5"}, "trigger": "requested", "ran": True}],
    )
    main._close_episode(USER, result)
    assert fake.get(USER, yes)["action"]["note_ids"] == ["note-0000-1111"]

    note = notes.create(USER, make_note("submit the form by 5", id="note-0000-1111"))
    notes.delete(USER, note.id)
    assert fake.get(USER, yes) is None


def test_a_follow_up_question_keeps_the_note_id(stores):
    thread = [{"role": "user", "content": "remind me about this: submit the form"}]
    intent_surface._stash_pending_confirmation(USER, thread, note_ids=["note-0000-1111"])
    assert intent_surface._pop_pending_thread(USER) == (thread, ["note-0000-1111"])
    # The old reader still gets just the thread.
    intent_surface._stash_pending_confirmation(USER, thread)
    assert intent_surface._pop_pending_confirmation(USER) == thread


def test_pending_state_is_dropped_on_delete(stores):
    note = notes.create(USER, make_note(CODE))
    intent_surface._stash_reminder_offer(USER, CODE, note.id)
    intent_surface._stash_pending_confirmation(USER, [{"role": "user", "content": CODE}])
    intent_surface._stash_reminder_offer(OTHER, "theirs")

    notes.delete(USER, note.id)

    assert str(USER) not in intent_surface._PENDING_REMINDER_OFFER
    assert str(USER) not in intent_surface._PENDING_CONFIRMATION
    assert str(OTHER) in intent_surface._PENDING_REMINDER_OFFER


# --- facts read out of those turns ------------------------------------------------

def _stated(text, episode_id, also_from=(), quote=""):
    return Fact(text=text, category=["facts"], stated_at=datetime.now(timezone.utc), metadata={
        "source": "stated", "episode_id": episode_id, "also_from": list(also_from), "quote": quote,
    })


def test_facts_derived_from_its_turn_go_too(stores):
    note, episode_id = _save_by_voice(stores)
    only_here = persona.upsert(USER, _stated("Gate code is 4417", episode_id, quote=CODE))
    elsewhere = _voice_episode(stores["memory"], "I live at number 12")
    shared = persona.upsert(USER, _stated("Lives at number 12", episode_id, also_from=[elsewhere], quote=CODE))

    notes.delete(USER, note.id)

    ids = {f.id for f in persona.all_facts(USER)}
    assert only_here not in ids
    assert shared in ids
    kept = persona.get(USER, shared).metadata
    assert kept["episode_id"] == elsewhere
    assert kept["also_from"] == []
    assert kept["quote"] == ""


# --- the pipeline, and the logs ----------------------------------------------------

def test_a_read_back_finishing_after_delete_writes_nothing(stores, capsys):
    note = notes.create(USER, make_note(CODE))

    class SlowInterpreter:
        def interpret(self, n):
            notes.delete(USER, n.id)  # the user deletes it while it is being read
            return "The gate code is 4417."

    NotesPipelineProcessor(summariser=MockSummariser(), interpreter=SlowInterpreter()).interpret(note)

    assert notes.all_notes(USER) == []
    assert "Traceback" not in capsys.readouterr().out


def test_logs_hold_no_note_text_by_default(stores, capsys, monkeypatch):
    monkeypatch.delenv("NOVA_LOG_CONTENT", raising=False)
    note, _ = _save_by_voice(stores)
    notes.delete(USER, note.id)
    out = capsys.readouterr().out
    assert "4417" not in out
    assert note.id in out  # ids are still logged


def test_logs_hold_content_when_asked(stores, capsys, monkeypatch):
    monkeypatch.setenv("NOVA_LOG_CONTENT", "1")
    _save_by_voice(stores)
    assert "4417" in capsys.readouterr().out


def test_a_short_reference_never_reaches_the_database():
    with pytest.raises(ValueError):
        memory.delete_referencing(USER, "")


def test_the_phone_is_told_which_notes_a_turn_quoted():
    """So it can tag the turn's Voice history bubbles (phase 2, S9)."""
    out = main._to_response(intent_surface.IntentResult(
        event_id=uuid4(), speech="Okay, 5pm.", note_ids=["note-0000-1111"]))
    assert out.note_ids == ["note-0000-1111"]


# --- the backups (phase 4): every row that went is journalled, by id ------------

def _journalled(stores, table, kind="delete"):
    return {r["row_id"] for r in stores["journal"] if r["table_name"] == table and r["kind"] == kind}


def test_a_delete_journals_the_note_its_episodes_and_facts(stores):
    note, episode_id = _save_by_voice(stores)
    derived = persona.upsert(USER, _stated("Gate code is 4417", episode_id, quote=CODE))
    elsewhere = _voice_episode(stores["memory"], "I live at number 12")
    shared = persona.upsert(USER, _stated("Lives at number 12", episode_id, also_from=[elsewhere], quote=CODE))

    notes.delete(USER, note.id)

    assert _journalled(stores, "notes") == {note.id}
    assert _journalled(stores, "episodic_memory") == {episode_id}
    assert _journalled(stores, "persona") == {derived}
    # Survives in the live database without the note: the dumps get its new metadata.
    assert _journalled(stores, "persona", "overwrite") == {shared}
    assert all(r["user_id"] == str(USER) for r in stores["journal"])
    # Ids only.
    assert CODE not in json.dumps(stores["journal"])


def test_a_promoted_fact_is_journalled_with_the_note(stores):
    note = notes.create(USER, make_note(CODE))
    fact_id = notes.promote(USER, note.id, ["facts"])
    notes.delete(USER, note.id)
    assert fact_id in _journalled(stores, "persona")


def test_the_journal_refuses_tables_the_scrub_cant_clean():
    from app.store import deletion_journal

    with pytest.raises(ValueError):
        deletion_journal.record(USER, "reminders", ["r1"])
