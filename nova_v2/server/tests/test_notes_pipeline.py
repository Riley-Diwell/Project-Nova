"""Chunking and summarising."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from notes_support import *  # noqa: F401,F403 - fixtures
from notes_support import USER, make_note

from app.notes_pipeline import chunking, summarise
from app.notes_pipeline.processor import NotesPipelineProcessor
from app.notes_pipeline.summarise import Summariser, SummaryFailed
from app.store import notes
from app.store.notes import Note, NoteSegment, NoteSummary

NOW = datetime(2026, 9, 23, 10, 5, tzinfo=timezone.utc)


def note_of(text, **kw):
    return Note(id="n", created_at=NOW, updated_at=NOW, source="device_voice",
                kind=kw.pop("kind", "capture"), text=text, **kw)


# --- chunking -------------------------------------------------------------------

def test_short_notes_are_not_chunked():
    assert chunking.chunk_note(note_of("just a few words")) == []


@pytest.mark.parametrize("n_words", [201, 400, 1000, 1537])
def test_chunk_bounds_and_overlap(n_words):
    words = [f"w{i}" for i in range(n_words)]
    chunks = chunking.chunk_note(note_of(" ".join(words)))
    assert all(len(c.text.split()) <= 300 for c in chunks)
    covered = set()
    for c in chunks:
        covered.update(c.text.split())
    assert covered == set(words)
    for a, b in zip(chunks, chunks[1:]):
        assert a.text.split()[-chunking.OVERLAP_WORDS:] == b.text.split()[:chunking.OVERLAP_WORDS]
    assert [c.idx for c in chunks] == list(range(len(chunks)))


def test_chunk_timestamps_are_monotonic():
    segments = [NoteSegment(start_s=i * 10.0, end_s=i * 10.0 + 9.5, text=" ".join(f"s{i}w{j}" for j in range(25)))
                for i in range(40)]
    note = note_of(" ".join(s.text for s in segments), segments=segments)
    chunks = chunking.chunk_note(note)
    assert len(chunks) > 1
    for c in chunks:
        assert c.start_s is not None and c.end_s is not None and c.start_s <= c.end_s
    starts = [c.start_s for c in chunks]
    assert starts == sorted(starts)
    assert chunks[-1].end_s <= segments[-1].end_s


# --- summarising ----------------------------------------------------------------

class FakeClient:
    """Stands in for the OpenAI-compatible client llm.parse calls - records each
    call and replies with `parsed` as JSON (or with text that isn't JSON)."""

    def __init__(self, parsed=None, finish_reason="stop", raises=None):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._parsed, self._finish, self._raises = parsed, finish_reason, raises

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises:
            raise self._raises
        content = self._parsed.model_dump_json() if self._parsed else "I can't summarise that."
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=content), finish_reason=self._finish)])


def long_text(n=200):
    return " ".join(f"point{i}" for i in range(n))


def test_summariser_sends_schema_and_delimited_transcript():
    client = FakeClient(parsed=NoteSummary(title="Essay structure", tldr="Plan the essay."))
    summary = Summariser(client=client).summarise(note_of(long_text()))
    assert summary.title == "Essay structure"
    [call] = client.calls
    assert call["response_format"]["json_schema"]["name"] == "NoteSummary"
    assert call["model"] == summarise.MODEL
    [system, user] = call["messages"]
    assert system["role"] == "system" and user["role"] == "user"
    assert "<transcript>" in user["content"]


@pytest.mark.parametrize("client", [
    FakeClient(raises=RuntimeError("network down")),
    FakeClient(parsed=None),                                          # never valid JSON
    FakeClient(parsed=NoteSummary(title="t", tldr="x"), finish_reason="length"),
])
def test_summariser_failures_raise(client):
    with pytest.raises(SummaryFailed):
        Summariser(client=client).summarise(note_of(long_text()))


def test_processor_skips_short_notes_and_marks_failures(stores):
    calls = []

    class Boom:
        def summarise(self, note):
            calls.append(note.id)
            raise SummaryFailed("nope")

    processor = NotesPipelineProcessor(summariser=Boom())
    short = notes.create(USER, make_note("ask tutor about Q3"))
    assert processor.after_create(short).summary_status == "none"
    assert calls == []

    long = notes.create(USER, make_note(long_text(), kind="dictation"))
    assert processor.after_create(long).summary_status == "failed"
    assert calls == [long.id]
