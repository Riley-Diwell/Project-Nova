"""The memory tool over the notes store."""
from datetime import datetime, timezone

from notes_support import *  # noqa: F401,F403 - fixtures

from notes_support import OTHER, USER

from app.core.request_user import as_user
from app.store import notes, persona
from app.tools.functions.memory_tool import MemoryTool

TOOL = MemoryTool()


def save(text, **kw):
    return TOOL.invoke({"action": "save", "text": text, **kw})


def recall(**kw):
    return TOOL.invoke({"action": "recall", **kw})


def test_save_writes_a_note_and_no_episode(stores):
    result = save("Parked on level 3")
    assert result["success"]
    assert [n.text for n in notes.all_notes(USER)] == ["Parked on level 3"]
    assert notes.all_notes(USER)[0].source == "assistant"
    assert stores["memory"].rows == []  # no `note` episodes any more
    assert persona.all_facts(USER) == []    # no category -> not promoted


def test_category_saves_a_fact_and_no_note(stores):
    # A one-line preference is something Nova knows, not a note.
    result = save("Loves a long black with sugar", category=["opinions", "likes", "drinks"],
                  episode_id="ep-1")
    assert result["success"] and result["indexed"]
    assert "note_id" not in result
    assert notes.all_notes(USER) == []
    [fact] = persona.all_facts(USER)
    assert fact.id == result["fact_id"]
    assert fact.metadata["source"] == "stated"
    assert fact.metadata["episode_id"] == "ep-1"
    assert "note_id" not in fact.metadata


def test_forgetting_a_saved_fact_tombstones_its_episode(stores):
    # So consolidation can't re-extract it from the turn it was said in.
    result = save("Is allergic to peanuts", category=["facts", "health"], episode_id="ep-2")
    persona.delete(USER, result["fact_id"])
    assert persona.all_facts(USER) == []
    assert "episode:ep-2" in persona.forgotten(USER)


def test_durable_save_falls_back_to_a_note_when_persona_is_down(stores, monkeypatch):
    def down(*_a, **_kw):
        raise RuntimeError("persona unreachable")
    monkeypatch.setattr(persona, "upsert", down)
    result = save("Is allergic to peanuts", category=["facts", "health"])
    assert result["success"] and not result["indexed"]
    assert [n.text for n in notes.all_notes(USER)] == ["Is allergic to peanuts"]


def test_recall_returns_titles_and_snippets_not_transcripts(stores):
    save("Ask tutor about Q3")
    result = recall(query="Q3")
    assert result["success"] and result["count"] >= 1
    hit = next(h for h in result["notes"] if h["tier"] == "note")
    assert set(hit) >= {"note_id", "created_at", "title", "tldr", "snippet", "kind"}
    assert "text" not in hit
    assert hit["note_id"] in result["note_ids"]


def test_recall_since_until_are_local_times(stores):
    notes.create(USER, notes.NoteIn(id="44444444-4444-4444-4444-444444444444", text="tutorial prep",
                              created_at=datetime(2026, 9, 22, 23, 30, tzinfo=timezone.utc)))
    # 09:30 on the 23rd in Canberra (+600).
    inside = recall(since="2026-09-23T00:00:00", until="2026-09-23T23:59:59", utc_offset_minutes=600)
    outside = recall(since="2026-09-24T00:00:00", utc_offset_minutes=600)
    assert [h["note_id"] for h in inside["notes"] if h["tier"] == "note"] == ["44444444-4444-4444-4444-444444444444"]
    assert [h for h in outside["notes"] if h["tier"] == "note"] == []


def test_gain_description_no_longer_overpromises():
    # The tool is open-loop; the Gain screen must not claim it saves
    # things unasked.
    assert TOOL.error(None) is None
    assert "at 1.0" not in TOOL.gain_description.lower()


def test_recall_only_sees_the_signed_in_users_notes(stores):
    notes.create(OTHER, notes.NoteIn(id="55555555-5555-5555-5555-555555555555", text="their locker code 4412"))
    save("My locker code is 1234")
    hits = [h for h in recall(query="locker code")["notes"] if h["tier"] == "note"]
    assert [h["snippet"] for h in hits] == ["My locker code is 1234"]
    with as_user(OTHER):
        hits = [h for h in recall(query="locker code")["notes"] if h["tier"] == "note"]
    assert [h["snippet"] for h in hits] == ["their locker code 4412"]


def test_save_lands_in_the_signed_in_users_notes(stores):
    with as_user(OTHER):
        save("Parked on level 3")
    assert notes.all_notes(USER) == []
    assert [n.text for n in notes.all_notes(OTHER)] == ["Parked on level 3"]


def test_no_signed_in_user_means_no_notes(stores):
    notes.create(USER, notes.NoteIn(id="66666666-6666-6666-6666-666666666666", text="ask tutor about Q3"))
    with as_user(None):
        assert recall(query="Q3")["success"] is False
        assert save("anything")["success"] is False
