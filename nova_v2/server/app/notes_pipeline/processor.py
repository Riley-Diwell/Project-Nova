"""The NoteProcessor implementation - chunk, embed, summarise, and read a
spoken note back.

The notes router (and the memory tool, for notes the assistant saves) calls
after_create/after_edit; this module never edits the store's code, only calls
its API (notes.replace_chunks, notes.set_summary, notes.set_interpretation).
Each step is best-effort and independent: a note whose summary fails is still
saved, chunked and searchable, just with summary_status="failed". Every write is
made as the note's owner (Note.user_id, set by the store from the verified
token), so the pipeline can never touch another user's note.

Reading a spoken note back (interpret.py) runs in the background: it is a
model call of a few seconds that nobody is waiting on.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from app.notes_pipeline.chunking import chunk_note
from app.notes_pipeline.interpret import Interpreter, should_interpret
from app.notes_pipeline.summarise import (
    MockSummariser,
    SummaryFailed,
    Summariser,
    should_summarise,
)
from app.store import notes
from app.store.notes import Note, NoteNotFound


def _mock_llm() -> bool:
    return os.environ.get("NOVA_MOCK_LLM", "").strip().lower() in ("1", "true", "yes")


# Where the read-back runs: a function that runs a callable later, or None to
# skip it (tests, which call interpret() directly). One worker: the model
# serves one request at a time anyway.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="notes-interpret")
_background: Optional[Callable[[Callable[[], None]], Any]] = _executor.submit


def set_background(runner: Optional[Callable[[Callable[[], None]], Any]]) -> None:
    global _background
    _background = runner


class NotesPipelineProcessor:
    def __init__(self, summariser: Optional[Any] = None, interpreter: Optional[Any] = None) -> None:
        self._summariser = summariser or (MockSummariser() if _mock_llm() else Summariser())
        # No read-back under NOVA_MOCK_LLM: there is no model to ask.
        self._interpreter = interpreter or (None if _mock_llm() else Interpreter())

    def after_create(self, note: Note, summarise: bool = True) -> Note:
        self._rechunk(note)
        if summarise and should_summarise(note):
            self.summarise(note)
        if self._interpreter is not None and _background is not None and should_interpret(note):
            _background(lambda: self.interpret(note))
        return notes.get(note.user_id, note.id)

    def interpret(self, note: Note) -> None:
        """Store Nova's reading of a spoken note - unless the user has edited
        the text since, in which case their words win and this is dropped."""
        try:
            meant = self._interpreter.interpret(note)
            current = notes.get(note.user_id, note.id)
            if current.text != note.text:
                return
            notes.set_interpretation(note.user_id, note.id, meant)
            if meant:
                print(f"[notes pipeline] read back {note.id}: {meant[:80]!r}")
        except NoteNotFound:
            pass  # deleted while it was being read
        except Exception as e:
            print(f"[notes pipeline] read-back failed for {note.id}: {e}")

    def after_edit(self, note: Note) -> Note:
        # Text changed: the chunks describe the old text, so they are redone
        # now. The summary is not - the store has marked it stale, and
        # re-summarising is the user's call (Re-summarise), not a surprise
        # model call on every typo fix.
        self._rechunk(note)
        return notes.get(note.user_id, note.id)

    def summarise(self, note: Note) -> Note:
        """Summarise now, whatever the length - Re-summarise and the retry
        endpoint call this directly."""
        try:
            summary = self._summariser.summarise(note)
        except SummaryFailed as e:
            print(f"[notes pipeline] summary failed for {note.id}: {e}")
            notes.set_summary(note.user_id, note.id, note.summary, "failed")
            return notes.get(note.user_id, note.id)
        notes.set_summary(note.user_id, note.id, summary, "done")
        print(f"[notes pipeline] summarised {note.id}: {summary.title!r}")
        return notes.get(note.user_id, note.id)

    def _rechunk(self, note: Note) -> None:
        try:
            chunks = chunk_note(note)
            notes.replace_chunks(note.user_id, note.id, chunks)
            if chunks:
                print(f"[notes pipeline] {note.id}: {len(chunks)} chunk(s)")
        except Exception as e:
            # Search still has the note-level embedding; it just can't see past
            # the first 512 tokens until the next edit re-chunks.
            print(f"[notes pipeline] chunking failed for {note.id}: {e}")
