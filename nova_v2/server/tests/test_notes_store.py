"""The notes store: CRUD, hybrid search, and the delete cascade into Persona."""
from datetime import datetime, timedelta, timezone

import pytest

from notes_support import *  # noqa: F401,F403 - fixtures
from notes_support import OTHER, USER, make_note

from app.store import notes, persona
from app.store.notes import NoteChunkIn, NotePatch, NoteQuery, NoteSummary
from app.store.notes import scoring


def test_create_is_idempotent_on_id(stores):
    first = notes.create(USER, make_note("ask tutor about Q3", id="11111111-1111-1111-1111-111111111111"))
    again = notes.create(USER, make_note("something else entirely", id=first.id))
    assert again.text == "ask tutor about Q3"
    assert len(notes.all_notes(USER)) == 1


def test_list_is_newest_first_and_filters_kind(stores):
    old = notes.create(USER, make_note("old", created_at=datetime.now(timezone.utc) - timedelta(days=1)))
    new = notes.create(USER, make_note("new capture", kind="capture"))
    assert [n.id for n in notes.list_notes(USER)] == [new.id, old.id]
    assert [n.id for n in notes.list_notes(USER, kind="capture")] == [new.id]


def test_exact_token_search_finds_course_codes(stores):
    """FakeEmbedder is not semantic, so this only passes through the token half -
    exactly the case ('Q3', 'COMP2100') the token half exists for."""
    target = notes.create(USER, make_note("ask tutor about Q3 before COMP2100"))
    notes.create(USER, make_note("buy milk"))
    hits = notes.search(USER, NoteQuery(text="what was Q3"))
    assert [h.note.id for h in hits] == [target.id]
    assert "Q3" in hits[0].snippet


def test_self_match_by_embedding(stores):
    target = notes.create(USER, make_note("parked on level 3"))
    hits = notes.search(USER, NoteQuery(text="parked on level 3"))
    assert hits and hits[0].note.id == target.id


def test_search_time_window_and_kind(stores):
    now = datetime.now(timezone.utc)
    yesterday = notes.create(USER, make_note("lecture notes", kind="capture", created_at=now - timedelta(days=1)))
    notes.create(USER, make_note("lecture notes today", kind="capture", created_at=now))
    hits = notes.search(USER, NoteQuery(text="lecture", since=now - timedelta(days=2), until=now - timedelta(hours=12)))
    assert [h.note.id for h in hits] == [yesterday.id]
    assert notes.search(USER, NoteQuery(text="lecture", kind="quick")) == []


def test_search_finds_content_only_in_a_late_chunk(stores):
    """Recall finds something said at minute 8 of a long capture."""
    filler = " ".join(f"word{i}" for i in range(1500))
    note = notes.create(USER, make_note(filler + " the exam covers eigenvalues", kind="capture"))
    notes.replace_chunks(USER, note.id, [
        NoteChunkIn(idx=0, text="word0 word1 word2", start_s=0, end_s=60),
        NoteChunkIn(idx=7, text="the exam covers eigenvalues", start_s=480, end_s=540),
    ])
    hits = notes.search(USER, NoteQuery(text="the exam covers eigenvalues"))
    assert hits[0].note.id == note.id
    assert hits[0].snippet_start_s == 480
    assert "eigenvalues" in hits[0].snippet


def test_quick_notes_decay_captures_do_not():
    now = datetime.now(timezone.utc)
    month_ago = now - timedelta(days=28)
    fresh = scoring.combined_score(0.5, 0.0, "quick", now, now)
    stale = scoring.combined_score(0.5, 0.0, "quick", month_ago, now)
    assert stale < fresh
    assert stale >= fresh * 0.5
    assert scoring.combined_score(0.5, 0.0, "capture", month_ago, now) == fresh


def test_patch_reembeds_and_marks_summary_stale(stores):
    note = notes.create(USER, make_note("cue three is due"))
    notes.set_summary(USER, note.id, NoteSummary(title="t", tldr="x"), "done")
    edited = notes.update(USER, note.id, NotePatch(text="Q3 is due"))
    assert edited.summary_status == "stale"
    assert notes.search(USER, NoteQuery(text="Q3 is due"))[0].note.id == note.id


def test_delete_cascades_to_promoted_facts_and_tombstones(stores):
    note = notes.create(USER, make_note("allergic to peanuts"))
    fact_id = notes.promote(USER, note.id, ["facts", "health"])
    assert persona.get(USER, fact_id).metadata["note_id"] == note.id

    notes.delete(USER, note.id)

    assert notes.all_notes(USER) == []
    assert persona.all_facts(USER) == []
    assert persona.note_key(note.id) in persona.forgotten(USER)


def test_deleting_the_fact_keeps_the_note(stores):
    note = notes.create(USER, make_note("allergic to peanuts"))
    fact_id = notes.promote(USER, note.id, ["facts", "health"])
    persona.delete(USER, fact_id)
    assert notes.get(USER, note.id).text == "allergic to peanuts"
    assert persona.note_key(note.id) in persona.forgotten(USER)


def test_delete_all(stores):
    for text in ("a", "b", "c"):
        notes.create(USER, make_note(text))
    assert notes.delete_all(USER) == 3
    assert notes.all_notes(USER) == []


# --- isolation between accounts --------------------------------------------------

def test_other_users_notes_are_invisible(stores):
    mine = notes.create(USER, make_note("ask tutor about Q3"))
    theirs = notes.create(OTHER, make_note("their secret about Q3"))

    assert [n.id for n in notes.list_notes(USER)] == [mine.id]
    assert [h.note.id for h in notes.search(USER, NoteQuery(text="Q3"))] == [mine.id]
    assert [h.note.id for h in notes.search(OTHER, NoteQuery(text="Q3"))] == [theirs.id]
    with pytest.raises(notes.NoteNotFound):
        notes.get(USER, theirs.id)


def test_cannot_change_or_delete_another_users_note(stores):
    theirs = notes.create(OTHER, make_note("theirs"))
    with pytest.raises(notes.NoteNotFound):
        notes.update(USER, theirs.id, NotePatch(text="mine now"))
    with pytest.raises(notes.NoteNotFound):
        notes.delete(USER, theirs.id)
    with pytest.raises(notes.NoteNotFound):
        notes.replace_chunks(USER, theirs.id, [])
    assert notes.get(OTHER, theirs.id).text == "theirs"


def test_delete_all_only_touches_own_notes(stores):
    notes.create(USER, make_note("mine"))
    notes.create(OTHER, make_note("theirs"))
    assert notes.delete_all(USER) == 1
    assert [n.text for n in notes.all_notes(OTHER)] == ["theirs"]


def test_reusing_another_users_id_is_a_conflict_not_a_read(stores):
    theirs = notes.create(OTHER, make_note("theirs"))
    with pytest.raises(notes.NoteIdConflict):
        notes.create(USER, make_note("probe", id=theirs.id))


def test_promoted_fact_records_its_owner(stores):
    note = notes.create(USER, make_note("allergic to peanuts"))
    fact_id = notes.promote(USER, note.id, ["facts", "health"])
    assert persona.get(USER, fact_id).metadata["user_id"] == str(USER)
