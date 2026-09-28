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

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from app.core.request_user import request_user_id
from app.store import notes
from app.store import persona
from app.tools.core.base import BaseTool

# How many hits a recall hands back. A prompt-size cap: every hit goes to the
# model as text.
RECALL_LIMIT = 20

# Floor for semantic Persona hits - a noise-drop, not a relevance test. See
# intent_surface.py's PERSONA_MIN_SIMILARITY for the measurements.
RECALL_MIN_SIMILARITY = 0.35

# How close a new note's embedding has to be to an existing belief in the SAME
# category before it is treated as an update to that belief rather than a new
# one - "likes bagels" then "dislikes bagels" should leave one fact, not two
# that both surface forever. Unmeasured against the real embedder; tune once
# real saves give a distribution.
CONTRADICTION_MIN_SIMILARITY = 0.8

class MemoryTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="memory",
            description=(
                "The user's personal notebook - their notes, including ones "
                "they asked Nova to remember and voice notes they dictated "
                "on their device. Two actions. "
                "Use action 'save' whenever the user asks you to remember or "
                "keep track of something ('remember I parked on level 3', "
                "'remember I love a long black with sugar'), and put what they "
                "want remembered in `text`, written as a statement about them. "
                "Something durable about who they are (give it a `category`) "
                "is filed into what Nova knows about them, not the Notes tab; "
                "only situational things are kept as a short note. "
                "Use action 'recall' whenever they ask what they noted, "
                "recorded or captured, or ask about something they may have "
                "told you earlier ('where did I park?', 'what did I note about "
                "the assignment?', 'summarise today's lecture notes'). Pass "
                "`query` to narrow the search, and `since`/`until` when they "
                "name a time ('yesterday's lecture', 'this morning'). Each hit "
                "comes back with a title, a one-line summary (tldr) and the "
                "passage that matched - answer from those; you never get a "
                "whole transcript. Expect to skim the hits and pick the "
                "relevant ones yourself. Call recall before telling the user "
                "you don't know something about them."
            ),
            gain_description=(
                "How readily Nova files and looks things up without being "
                "asked. At 1.0 anything the user states as a fact is saved as "
                "a note - about themselves, about the world, in passing, "
                "whether or not it seems worth keeping and whether or not it "
                "was addressed to Nova - and any question is treated as a "
                "recall, answered with the closest match even when nothing "
                "matches outright. At 0.0 only an explicit instruction "
                "counts: 'note that', 'remember this', 'what did I note "
                "about…'. Everything in between raises the bar for acting on "
                "speech that was probably, but not certainly, meant for Nova. "
                "Judging a statement too trivial or too obvious to keep is a "
                "judgement for a lower gain to make, not this one."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["save", "recall"],
                        "description": (
                            "'save' = remember something (a durable fact about the user, "
                            "or a short situational note), 'recall' = look it back up."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": (
                            "Required for 'save'. What to remember, phrased as a "
                            "standalone statement that will still make sense weeks "
                            "later, e.g. 'Parked on level 3 of the Kambri car park'."
                        ),
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional for 'save'. A few short topic words to make "
                            "the note easier to find later, e.g. ['parking', 'car']."
                        ),
                    },
                    "category": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional for 'save'. Where this belongs in what Nova "
                            "knows about the user, general to specific. Give it "
                            "whenever the thing says something durable about who "
                            "they are - a preference, an opinion, a routine, a "
                            "course they take, a health fact - e.g. ['opinions', "
                            "'likes', 'food'] for 'likes bagels' or 'loves a long "
                            "black with sugar', or ['facts', 'health'] for "
                            "'allergic to peanuts'. It is then saved only as "
                            "something Nova knows, never as a note. Omit it for "
                            "anything situational or one-off ('parked on level "
                            "3', 'meeting moved to 3pm') - those are kept as a "
                            "short note and are not remembered as part of who "
                            "the user is."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Optional for 'recall'. What to look for, in the "
                            "user's own words. Matched by meaning and by exact "
                            "words (course codes, question numbers). Omit it to "
                            "get the most recent notes in the time range."
                        ),
                    },
                    "since": {
                        "type": "string",
                        "description": (
                            "Optional for 'recall'. Only notes taken at or after "
                            "this LOCAL time, ISO 8601 with no timezone suffix, "
                            "e.g. '2026-09-22T00:00:00' - resolve 'yesterday' or "
                            "'this morning' against local_time."
                        ),
                    },
                    "until": {
                        "type": "string",
                        "description": (
                            "Optional for 'recall'. Only notes taken at or before "
                            "this LOCAL time, same format as `since`."
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["quick", "dictation"],
                        "description": (
                            "Optional for 'recall'. 'dictation' = longer spoken notes, "
                            "'quick' = short notes. Omit to search all."
                        ),
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
) -> dict[str, Any]:
    """A durable thing (filed under a category) becomes a belief; anything
    situational becomes a short note. Never both - see the module docstring."""
    if not text:
        return {"success": False, "spoken": "I'm not sure what you'd like me to remember."}

    if category:
        fact_id = _save_fact(user_id, text, tags, category, episode_id)
        if fact_id is not None:
            return {
                "success": True,
                "fact_id": fact_id,
                "fact": text,
                "indexed": True,
                "spoken": "Got it, I'll remember that.",
            }
        # Persona unreachable: keep it as a note rather than lose something the
        # user asked for. They can still file it from the note later.
        print("[memory tool] persona unreachable - keeping the durable save as a note")

    return _save_note(user_id, text, tags)


def _save_fact(
    user_id: UUID, text: str, tags: list[str], category: list[str], episode_id: Optional[str],
) -> Optional[str]:
    """File a durable statement straight into Persona (Section 5.4) as a belief
    about the user. The id, or None if Persona couldn't be reached.

    Before writing, checks whether this updates a belief already in the same
    category (e.g. "likes bagels" -> "dislikes bagels") and, if so, updates
    that fact in place rather than adding a second one.

    Written as a stated fact carrying the turn's `episode_id` - the same shape
    the consolidation statement pass writes - so that pass treats the episode
    as already read instead of extracting the same belief a second time, and
    forgetting the fact tombstones `episode:<id>` so it stays forgotten.
    """
    existing_id = _find_contradicted(user_id, text, category)
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
        fact_id = persona.upsert(user_id, persona.Fact(
            id=existing_id, text=text, category=category, metadata=metadata,
        ))
    except Exception as e:
        print(f"[memory tool] persona save failed: {e}")
        return None

    verb = f"updated persona fact {fact_id} (was {existing_id!r})" if existing_id else f"saved persona fact {fact_id}"
    print(f"[memory tool] {verb}: {text!r}")
    return fact_id


def _save_note(user_id: UUID, text: str, tags: list[str]) -> dict[str, Any]:
    """Keep a situational thing as a short note in the Notes tab."""
    # Non-fatal, as everywhere a store is touched: an unreachable backend must
    # not take the whole turn down - but unlike a background write, the user
    # asked for this, so say it didn't land.
    try:
        note = notes.create(user_id, notes.NoteIn(
            id=notes.new_note_id(),
            source="assistant",
            kind="quick",
            text=text,
            tags=tags,
            summarise="never",
        ))
    except Exception as e:
        print(f"[memory tool] save failed: {e}")
        return {"success": False, "spoken": "I couldn't save that just now - my notes aren't reachable."}

    print(f"[memory tool] saved note {note.id}: {text!r}")
    return {
        "success": True,
        "note_id": note.id,
        "note": text,
        "indexed": False,
        "spoken": "I've saved that as a note.",
    }


def _find_contradicted(user_id: UUID, text: str, category: list[str]) -> Optional[str]:
    """The id of an existing belief this statement supersedes, or None.

    Scoped to the same category path so "likes bagels" is only compared
    against other food opinions. Best-effort: a search failure is treated as
    no match, and the statement lands as a new fact rather than being lost.
    """
    try:
        matches = persona.search(user_id, persona.PersonaQuery(
            text=text, category=category, limit=1,
            min_similarity=CONTRADICTION_MIN_SIMILARITY,
        ))
    except Exception as e:
        print(f"[memory tool] contradiction search skipped: {e}")
        return None
    return matches[0].fact.id if matches else None


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

    print(f"[memory tool] recall query={query!r}: {len(matches)} persona matches")
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

    print(f"[memory tool] recall query={query!r} since={since} until={until} kind={kind}: "
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
    so the model can tell a belief from a note."""
    def label(h: dict[str, Any]) -> str:
        return h.get("title") or h.get("text") or h.get("snippet") or ""

    if not hits:
        spoken = "I haven't got any notes that match."
    elif len(hits) == 1:
        spoken = f"One note: {label(hits[0])}"
    else:
        ordering = "most relevant first" if ordered_by_relevance else "most recent first"
        spoken = f"{len(hits)} notes, {ordering}: " + "; ".join(label(h) for h in hits[:3])
        if len(hits) > 3:
            spoken += f" and {len(hits) - 3} more"

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
