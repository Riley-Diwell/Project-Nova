"""Splitting a long note into embedding windows.

bge-large-en-v1.5 embeds only the first 512 tokens of whatever it is given, so
a note-level embedding of a 25-minute capture sees the first two minutes and
nothing else - "recall finds something said at minute 8" is impossible without
this. Each chunk is embedded on its own (note_chunks) and search takes the best
chunk per note.

~200 words with a 30-word overlap: 200 English words is ~260 tokens, well
under bge's 512 even for jargon-heavy lecture text, and the overlap keeps a
sentence that straddles a boundary findable from either side.

Timestamps: when the note has segments (a capture, or a dictation with STT
word timings folded into segments), each word is placed in time by
interpolating across its segment, and a chunk's start_s/end_s are its first
and last word's times. That is what lets a search hit say "at 8:12".
"""
from __future__ import annotations

from typing import Optional

from app.store.notes import Note, NoteChunkIn

CHUNK_WORDS = 200
OVERLAP_WORDS = 30

# Below this a note is one chunk's worth anyway, and its note-level embedding
# already covers all of it - chunking would only store the same vector twice.
MIN_WORDS_TO_CHUNK = CHUNK_WORDS


def chunk_note(note: Note) -> list[NoteChunkIn]:
    """The chunks for `note`, or [] if it is short enough not to need any."""
    words = _timed_words(note)
    if len(words) <= MIN_WORDS_TO_CHUNK:
        return []

    chunks: list[NoteChunkIn] = []
    step = CHUNK_WORDS - OVERLAP_WORDS
    start = 0
    while start < len(words):
        window = words[start:start + CHUNK_WORDS]
        # A last window that is nothing but overlap adds no new text.
        if chunks and start + OVERLAP_WORDS >= len(words):
            break
        chunks.append(NoteChunkIn(
            idx=len(chunks),
            text=" ".join(w for w, _ in window),
            start_s=window[0][1],
            end_s=window[-1][1],
        ))
        if start + CHUNK_WORDS >= len(words):
            break
        start += step
    return chunks


def _timed_words(note: Note) -> list[tuple[str, Optional[float]]]:
    """Every word of the note with its time in the recording, if known."""
    if not note.segments:
        return [(w, None) for w in note.text.split()]

    timed: list[tuple[str, Optional[float]]] = []
    for seg in note.segments:
        seg_words = seg.text.split()
        if not seg_words:
            continue
        span = max(seg.end_s - seg.start_s, 0.0)
        for i, word in enumerate(seg_words):
            frac = i / len(seg_words)
            timed.append((word, round(seg.start_s + span * frac, 2)))
    return timed
