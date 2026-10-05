"""
tools/memory_tool.py - Function 2: note-taking, over the notes store  (Jay)

WHAT THIS FILE IS
The assistant's way into the user's notes (app/store/notes). Everything else
writes to episodic_memory automatically - main.py logs one row per event - but
that log is a record of what *happened*. This tool is how the user puts
something in deliberately through Nova, and asks for it back later:

    "remember I parked on level 3"               -> action="save"
    "remember I'm allergic to peanuts"           -> action="save", category=[...]
    "where did I park?"                          -> action="recall"
    "what did I note about the tutor yesterday?" -> action="recall", since/until

Voice notes captured on the device ("note ask tutor about Q3")
never come through here - the phone posts them straight to POST /notes,
verbatim. A "note ..." said to the assistant does come through here, but from
intent_surface._save_verbatim_note with the user's own words, never the
model's. This tool's model path is "remember...": the model rephrases, and
decides whether the thing is durable.

TWO STORES, TWO LIFETIMES - AND A SAVE GOES TO ONE OF THEM
`category` decides, and the model supplies it:

  - With a category, the thing is durable - "likes bagels" ->
    ["opinions","likes","food"] - and goes straight into Persona (Section 5.4)
    as a belief about the user, shown on the Knowledge Map. It is NOT also
    written as a note: a one-line preference in the Notes tab is clutter, and
    notes are for things worth keeping as a record (a transcript, a lecture, a
    poem, a dictated draft). The belief carries the turn's `episode_id`, which
    is its tombstone key if the user forgets it and what stops the
    consolidation statement pass re-extracting it from the same utterance.
  - Without one, it is situational - "parked on level 3" - and is kept as a
    short note: true for an hour, and recall's recency weighting lets it fade.
    Persona never expires, so it must not go there.

A note the user wants filed as a belief is still promoted from the note itself
("Add to what Nova knows" in the app, notes.promote()), linked back by
`note_id`.

RECALL READS BOTH, AND RETURNS MORE THAN IT WAS ASKED FOR
Durable facts ranked by meaning, plus notes from a hybrid search (meaning +
exact tokens, so "Q3" and "COMP2100" are found even though the embedding
barely sees them). Similarity cannot separate a relevant hit from an
irrelevant one here - measured, the bands overlap (see intent_surface.py's
PERSONA_MIN_SIMILARITY) - so every hit above a low floor goes back to the
model, labelled with where it came from, and the model picks.

Each note hit is {note_id, created_at, title, tldr, snippet, kind} - never a
whole transcript. A lecture capture is thousands of words; the snippet is the
passage that matched and the tldr is the summary written when it was saved.

DEGRADING
Persona needs Supabase and the embedding model; a durable save that cannot
reach it falls back to a note rather than being lost, and a recall that cannot
reach it still searches notes.
"""

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from app.core.request_user import request_user_id
from app.store import notes
from app.store import persona
from app.tools.core.base import BaseTool
from app.core.config import said

# How many hits a recall hands back. A prompt-size cap: every hit goes to the
# model as text.
RECALL_LIMIT = 20

# Floor for semantic Persona hits - a noise-drop, not a relevance test. See
# intent_surface.py's PERSONA_MIN_SIMILARITY for the measurements.
RECALL_MIN_SIMILARITY = 0.35

class MemoryTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="memory",
            description=(
                "The user's notebook - things they asked Nova to remember and "
                "voice notes dictated on their device. "
                "'save' when they ask you to remember something about "
                "themselves or their situation ('remember I parked on level "
                "3', 'remember I love a long black'). Never for something to "
                "do later ('remember to email Dr Chen') - that is "
                "set_reminder. Also 'save' when they ask you to write or make "
                "a note for them (class notes, a summary, a list): save the "
                "note itself, with a `title`. "
                "'recall' when they ask what they noted, recorded or captured, "
                "or about something they may have told you ('where did I "
                "park?', 'summarise today's lecture notes'), and before "
                "telling them you don't know something about them. Each hit "
                "is a title, a one-line tldr and the matching passage, never a "
                "whole transcript - skim them and answer from the relevant "
                "ones."
            ),
            # This tool is open-loop: error() returns None, so the
            # Controller only ever runs it on the user's explicit command and
            # never on its own, and a command bypasses gain entirely. The old
            # text promised "at 1.0 anything the user states as a fact is
            # saved", which the Gain screen showed users and which nothing
            # implemented. Say what is true.
            gain_description=(
                "Nova only saves or looks up notes when you ask it to - "
                "\"remember…\", \"what did I note about…\", or a voice note "
                "from your device. This dial doesn't change that yet: there is "
                "no situation in which Nova takes notes on its own."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["save", "recall"],
                        "description": "'save' = remember or write a note, 'recall' = look it back up.",
                    },
                    "text": {
                        "type": "string",
                        "description": (
                            "Required for 'save'. A statement about them that makes "
                            "sense weeks later - 'Parked on level 3 of the Kambri "
                            "car park'. A note you write: the whole note, read on "
                            "a phone, so '## ' headings, '- ' bullets, one point "
                            "per line, a blank line between sections - never one "
                            "long paragraph."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": (
                            "'save', only for a note you write: at most 8 words - "
                            "'DSP week 10 workshop prep'. Omit for 'remember ...'."
                        ),
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional for 'save'. A few topic words to find it by - ['parking', 'car'].",
                    },
                    "category": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "'save': give it for anything durable about who they "
                            "are - a preference, opinion, routine, course, health "
                            "fact - general to specific: ['opinions', 'likes', "
                            "'food'] for 'loves a long black', ['facts', 'health'] "
                            "for 'allergic to peanuts'. It is then filed as "
                            "something Nova knows, not a note. Omit for anything "
                            "situational ('parked on level 3', 'meeting moved to "
                            "3pm'), which is kept as a short note."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "'recall': what to look for, in their words - matched "
                            "by meaning and exact words (course codes). Omit for "
                            "the most recent notes in the time range."
                        ),
                    },
                    "since": {
                        "type": "string",
                        "description": (
                            "'recall', when they name a time ('yesterday's "
                            "lecture'): notes from this LOCAL time on, ISO 8601 "
                            "with no suffix, from local_time."
                        ),
                    },
                    "until": {
                        "type": "string",
                        "description": "'recall': notes up to this LOCAL time, same format as `since`.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["quick", "dictation"],
                        "description": "'recall': 'dictation' = longer spoken notes, 'quick' = short. Omit for all.",
                    },
                },
                "required": ["action"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        action = tool_input.get("action", "recall")

        # The signed-in user this /event request is for (core/request_user.py),
        # never anything from tool_input - the model can't choose whose notes
        # it reads. No user means no notes, rather than someone else's.
        user_id = request_user_id()
        if user_id is None:
            print("[memory tool] no signed-in user bound to this request")
            return {"success": False, "spoken": "I can't reach your notes right now."}

        if action == "save":
            episode_id = tool_input.get("episode_id")
            return _save(
                user_id,
                text=str(tool_input.get("text") or "").strip(),
                tags=[str(t) for t in (tool_input.get("tags") or [])],
                category=[str(c) for c in (tool_input.get("category") or [])],
                episode_id=str(episode_id) if episode_id else None,
                title=str(tool_input.get("title") or "").strip() or None,
                # Set by intent_surface._save_verbatim_note, never offered to
                # the model: these are the user's words as speech-to-text
                # heard them, so they get read back (notes_pipeline/interpret.py).
                heard=tool_input.get("heard") is True,
            )

        if action == "recall":
            offset = tool_input.get("utc_offset_minutes")
            offset = offset if isinstance(offset, int) else 0
            kind = tool_input.get("kind")
            return _recall(
                user_id,
                query=str(tool_input.get("query") or "").strip(),
                since=_local_to_utc(tool_input.get("since"), offset),
                until=_local_to_utc(tool_input.get("until"), offset),
                kind=kind if kind in ("quick", "dictation") else None,
            )

        return {"success": False, "spoken": "I didn't understand that memory action."}


# --- save -----------------------------------------------------------------------

def _save(
    user_id: UUID, text: str, tags: list[str], category: list[str], episode_id: Optional[str] = None,
    title: Optional[str] = None, heard: bool = False,
) -> dict[str, Any]:
    """A durable thing (filed under a category) becomes a belief; anything
    situational becomes a short note. Never both - see the module docstring.
    A note Nova wrote (it has a title) or one the user said word for word is
    always a note, whatever category came with it."""
    if not text:
        return {"success": False, "spoken": "I'm not sure what you'd like me to remember."}

    if category and not title and not heard:
        result = _save_fact(user_id, text, tags, category, episode_id)
        if result is not None:
            return _saved_fact(text, result)
        # Persona unreachable: keep it as a note rather than lose something the
        # user asked for. They can still file it from the note later.
        print("[memory tool] persona unreachable - keeping the durable save as a note")

    return _save_note(user_id, text, tags, title=title, heard=heard, episode_id=episode_id)


def _save_fact(
    user_id: UUID, text: str, tags: list[str], category: list[str], episode_id: Optional[str],
) -> Optional[persona.RememberResult]:
    """File a durable statement straight into Persona (Section 5.4) as a belief
    about the user. What happened to it, or None if Persona couldn't be reached.

    Through persona.remember(), so it can't sit next to a belief that says the
    same thing (merged) or the opposite (said now, so it replaces it -
    "likes bagels" -> "dislikes bagels" leaves one fact, in any category).

    Written as a stated fact carrying the turn's `episode_id` - the same shape
    the consolidation statement pass writes - so that pass treats the episode
    as already read instead of extracting the same belief a second time, and
    forgetting the fact tombstones `episode:<id>` so it stays forgotten.
    """
    metadata: dict[str, Any] = {
        "source": "stated",
        "origin": "assistant",
        "quote": text,
        "user_id": str(user_id),
        "tags": tags,
    }
    if episode_id:
        metadata["episode_id"] = episode_id
    try:
        result = persona.remember(
            user_id,
            persona.Fact(text=text, category=category, metadata=metadata),
            stated_at=datetime.now(timezone.utc),
            # The user is waiting on this turn: a short judge call, a few
            # neighbours, and on a timeout write it now and settle it after.
            judge=persona.get_judge(voice=True),
            candidate_limit=persona.VOICE_CANDIDATE_LIMIT,
        )
    except Exception as e:
        print(f"[memory tool] persona save failed: {e}")
        return None

    print(f"[memory tool] persona {result.action.value} {result.fact_id}: {said(text)}")
    if result.unreconciled:
        _reconcile_later(user_id)
    return result


def _saved_fact(text: str, result: persona.RememberResult) -> dict[str, Any]:
    """The tool result for a durable save, worded by what happened to it."""
    was = [s.text for s in result.superseded]
    if result.action == persona.RememberAction.MERGED:
        spoken = "I already knew that - got it."
    elif result.action == persona.RememberAction.REPLACED and was:
        spoken = f"Updated - that replaces what I had before: {was[0]}."
    else:
        spoken = "Got it, I'll remember that."
    out: dict[str, Any] = {
        "success": True,
        "fact_id": result.fact_id,
        "fact": text,
        "indexed": True,
        "outcome": result.action.value,
        "spoken": spoken,
    }
    if was:
        # So the model can say what changed in its own words.
        out["replaced"] = was
    return out


def _reconcile_later(user_id: UUID) -> None:
    """The judge didn't answer in time, so the fact went in unreconciled.
    Settle it off the request path with the patient batch judge, so any
    duplicate or contradiction it made lasts seconds, not until the next
    consolidation. Best-effort - consolidation sweeps whatever this misses."""
    store, judge = persona.get_store(), persona.get_judge()

    def run() -> None:
        try:
            persona.reconcile_all(user_id, only_unreconciled=True, judge=judge, store=store)
        except Exception as e:
            print(f"[memory tool] background reconcile failed: {e}")

    threading.Thread(target=run, name="persona-reconcile", daemon=True).start()


def _save_note(
    user_id: UUID, text: str, tags: list[str], title: Optional[str] = None, heard: bool = False,
    episode_id: Optional[str] = None,
) -> dict[str, Any]:
    """Keep it as a note in the Notes tab: a situational thing, a note Nova
    wrote for the user (with a title), or the user's own words as heard.

    `episode_id` is the voice turn that asked for it, kept as the note's
    origin: deleting the note deletes that turn too (notes.delete)."""
    # Non-fatal, as everywhere a store is touched: an unreachable backend must
    # not take the whole turn down - but unlike a background write, the user
    # asked for this, so say it didn't land.
    try:
        note = notes.create(user_id, notes.NoteIn(
            id=notes.new_note_id(),
            source="assistant",
            kind="quick",
            title=title,
            text=text,
            # An stt record marks the words as speech-to-text's, which is what
            # gets a note read back. The engine is the phone's; unknown here.
            stt=notes.NoteStt() if heard else None,
            tags=tags,
            summarise="never",
            origin_episode_id=episode_id,
        ))
    except Exception as e:
        print(f"[memory tool] save failed: {e}")
        return {"success": False, "spoken": "I couldn't save that just now - my notes aren't reachable."}

    print(f"[memory tool] saved note {note.id}: {said(text)}")
    _process(note)
    return {
        "success": True,
        "note_id": note.id,
        "note": text,
        "indexed": False,
        # Short and distinct from a memory save ("Got it, I'll remember
        # that"), so the user can hear which of the two just happened.
        "spoken": "Noted.",
    }


def _process(note: notes.Note) -> None:
    """What POST /notes does for a note the phone sends: chunk a long one for
    search, and read a spoken one back in the background. The note is saved
    either way - processing is an enhancement, and never fails the save."""
    processor = notes.get_processor()
    try:
        if hasattr(processor, "summarise"):
            processor.after_create(note, summarise=False)
        else:
            processor.after_create(note)
    except Exception as e:
        print(f"[memory tool] processing skipped for {note.id}: {e}")


# --- recall ---------------------------------------------------------------------

def _recall(
    user_id: UUID,
    query: str,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    kind: Optional[str] = None,
) -> dict[str, Any]:
    """Durable facts by meaning, then notes by hybrid search. Labelled by tier."""
    facts = _search_persona(user_id, query) if query and not (since or until or kind) else []
    found = _search_notes(user_id, query, since, until, kind)

    if found is None and not facts:
        return _unreachable()

    seen = {f["text"] for f in facts}
    merged: list[dict[str, Any]] = list(facts)
    for hit in (found or []):
        if hit["snippet"] not in seen:
            merged.append(hit)
            seen.add(hit["snippet"])

    return _recalled(merged[:RECALL_LIMIT], ordered_by_relevance=bool(query))


def _search_persona(user_id: UUID, query: str) -> list[dict[str, Any]]:
    """Semantic Persona hits, best first. [] if Persona is unreachable."""
    try:
        matches = persona.search(user_id, persona.PersonaQuery(
            text=query, limit=RECALL_LIMIT, min_similarity=RECALL_MIN_SIMILARITY,
        ))
    except Exception as e:
        print(f"[memory tool] persona search skipped: {e}")
        return []

    print(f"[memory tool] recall query={said(query)}: {len(matches)} persona matches")
    return [
        {
            "tier": "known_fact",
            "created_at": m.fact.created_at.isoformat() if m.fact.created_at else None,
            "text": m.fact.text,
            "category": m.fact.category,
            "note_id": (m.fact.metadata or {}).get("note_id"),
            "similarity": round(m.similarity, 3),
        }
        for m in matches
    ]


def _search_notes(
    user_id: UUID, query: str, since: Optional[datetime], until: Optional[datetime], kind: Optional[str],
) -> Optional[list[dict[str, Any]]]:
    """Notes-store hits, best first (newest first with no query). None if the
    store is unreachable - different from empty, and _recall treats it so."""
    try:
        matches = notes.search(user_id, notes.NoteQuery(
            text=query or None, since=since, until=until, kind=kind, limit=RECALL_LIMIT,
        ))
    except Exception as e:
        print(f"[memory tool] notes search failed: {e}")
        return None

    print(f"[memory tool] recall query={said(query)} since={since} until={until} kind={kind}: "
          f"{len(matches)} note(s)")
    return [
        {
            "tier": "note",
            "note_id": m.note.id,
            "created_at": m.note.created_at.isoformat(),
            "title": m.note.display_title(),
            "tldr": m.note.summary.tldr if m.note.summary else None,
            "snippet": m.snippet,
            "kind": m.note.kind,
            "context": m.note.context.calendar_title if m.note.context else None,
        }
        for m in matches
    ]


def _unreachable() -> dict[str, Any]:
    return {"success": False, "spoken": "I couldn't check my notes just now."}


def _recalled(hits: list[dict[str, Any]], ordered_by_relevance: bool) -> dict[str, Any]:
    """One result shape however the hits were found. Each hit carries `tier`
    so the model can tell a belief from a note - and the spoken line keeps them
    apart too, because something Nova knows about the user is not a note."""
    def label(h: dict[str, Any]) -> str:
        return h.get("title") or h.get("text") or h.get("snippet") or ""

    def first_three(some: list[dict[str, Any]]) -> str:
        line = "; ".join(label(h) for h in some[:3])
        return line + (f" and {len(some) - 3} more" if len(some) > 3 else "")

    known = [h for h in hits if h.get("tier") == "known_fact"]
    noted = [h for h in hits if h.get("tier") != "known_fact"]
    parts = []
    if known:
        parts.append(f"What I know: {first_three(known)}")
    if len(noted) == 1:
        parts.append(f"One note: {label(noted[0])}")
    elif noted:
        ordering = "most relevant first" if ordered_by_relevance else "most recent first"
        parts.append(f"{len(noted)} notes, {ordering}: {first_three(noted)}")
    spoken = ". ".join(parts) if parts else "I haven't got anything on that."

    return {
        "success": True,
        "count": len(hits),
        "notes": hits,
        # The Voice tab turns these into chips that open the note.
        "note_ids": [h["note_id"] for h in hits if h.get("tier") == "note"],
        "spoken": spoken,
    }


def _local_to_utc(value: Any, utc_offset_minutes: int) -> Optional[datetime]:
    """A model-supplied LOCAL ISO time as an aware UTC datetime, or None if it
    is missing or unparseable (a bad bound is dropped rather than failing the
    recall - an unbounded search is still an answer)."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        print(f"[memory tool] ignoring unparseable time bound {value!r}")
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)
    return (dt - timedelta(minutes=utc_offset_minutes)).replace(tzinfo=timezone.utc)
