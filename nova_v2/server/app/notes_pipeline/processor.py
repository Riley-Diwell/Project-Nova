"""The NoteProcessor implementation - chunk, embed, summarise.

The notes router calls after_create/after_edit; this module never edits the
store's code, only calls its API (notes.replace_chunks, notes.set_summary).
Each step is best-effort and independent: a note whose summary fails is still
saved, chunked and searchable, just with summary_status="failed". Every write is
made as the note's owner (Note.user_id, set by the store from the verified
token), so the pipeline can never touch another user's note.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from app.notes_pipeline.chunking import chunk_note
from app.notes_pipeline.summarise import (
    MockSummariser,
    SummaryFailed,
    Summariser,
    should_summarise,
)
from app.store import notes
from app.store.notes import Note


def _mock_llm() -> bool:
    return os.environ.get("NOVA_MOCK_LLM", "").strip().lower() in ("1", "true", "yes")


class NotesPipelineProcessor:
    def __init__(self, summariser: Optional[Any] = None) -> None:
        self._summariser = summariser or (MockSummariser() if _mock_llm() else Summariser())

    def after_create(self, note: Note, summarise: bool = True) -> Note:
        self._rechunk(note)
        if summarise and should_summarise(note):
            self.summarise(note)
        return notes.get(note.user_id, note.id)

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
