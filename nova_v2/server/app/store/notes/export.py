"""Rendering notes for export - Settings -> "Export my notes".

JSON is the lossless one (every field, round-trippable); Markdown is the one a
person reads or pastes into their own notes app. Neither includes embeddings:
they are derived, 1024 floats each, and meaningless outside this system.
"""
from __future__ import annotations

from typing import Any

from app.store.notes.models import Note


def to_json(notes: list[Note]) -> list[dict[str, Any]]:
    return [n.model_dump(mode="json") for n in notes]


def to_markdown(notes: list[Note]) -> str:
    parts = ["# Nova notes", ""]
    for note in notes:
        parts.append(note_markdown(note))
    return "\n".join(parts).rstrip() + "\n"


def note_markdown(note: Note) -> str:
    """One note as Markdown - also what the app's "Share note" sends."""
    lines = [f"## {note.display_title()}", "", f"*{_meta_line(note)}*", ""]

    if note.summary:
        s = note.summary
        lines += [f"**Summary:** {s.tldr}", ""]
        for heading, items in (
            ("Key points", s.key_points),
            ("Action items", s.action_items),
            ("Open questions", s.open_questions),
        ):
            if items:
                lines.append(f"**{heading}**")
                lines += [f"- {item}" for item in items]
                lines.append("")
        if s.flagged_moments:
            lines.append("**Flagged moments**")
            lines += [f"- [{_clock(m.t_s)}] \"{m.quote}\"" for m in s.flagged_moments]
            lines.append("")

    if note.segments:
        if note.stt and note.stt.engine:
            lines += ["*Auto-transcribed, may contain errors.*", ""]
        lines += [f"[{_clock(seg.start_s)}] {seg.text}" for seg in note.segments]
    else:
        lines.append(note.text)
    lines.append("")
    if note.tags:
        lines += ["Tags: " + ", ".join(note.tags), ""]
    return "\n".join(lines)


def _meta_line(note: Note) -> str:
    bits = [note.created_at.strftime("%a %d %b %Y %H:%M")]
    if note.context and note.context.calendar_title:
        bits.append(note.context.calendar_title)
    if note.duration_s:
        bits.append(f"{round(note.duration_s / 60)} min" if note.duration_s >= 60 else f"{int(note.duration_s)} s")
    bits.append(note.kind)
    return " · ".join(bits)


def _clock(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
