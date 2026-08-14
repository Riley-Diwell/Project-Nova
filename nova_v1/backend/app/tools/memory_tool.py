"""
tools/memory_tool.py - Function 2: note-taking, over the Memory store  (Jay)

WHAT THIS FILE IS
The user-facing half of the Memory store. Everything else writes to
episodic_memory automatically - main.py logs one row per event - but that log
is a record of what *happened*. This tool is how the user puts something in
deliberately and asks for it back later:

    "remember I parked on level 3"        -> action="save"
    "note that the thesis draft is due Friday"  -> action="save"
    "where did I park?"                   -> action="recall"
    "what have I asked you to remember?"  -> action="recall"

TWO STORES, TWO LIFETIMES
A save always lands in episodic_memory; it lands in Persona as well only if it
is durable. That split is the whole design:

  - memory.append() logs every note as an Episode (Section 5.5) - the
    append-only, chronological record that the user said this, when. Short-term
    detail lives here and only here: "parked on level 3" is true for an hour
    and meaningless the moment they drive away.
  - persona.upsert() additionally mirrors durable notes into the Persona vector
    store (Section 5.4), where they sit alongside the facts NOVA has worked out
    about the user and are searchable by meaning, indefinitely.

`category` is what decides, and the model supplies it: a note filed into the
ontology ("likes bagels" -> ["opinions","likes","food"]) is durable and gets
promoted; a note with no category is situational and stays episodic. Putting
"parked on level 3" in a store searched by meaning would be worse than not
storing it - it would surface forever, competing with what actually matters.

A recall reads BOTH tiers and returns them together - durable facts ranked by
meaning, then recent notes. Not either/or, because similarity cannot tell a
relevant fact from an irrelevant one: measured against the real store the two
bands overlap, so a persona hit is never evidence that the log holds nothing
better. "Where did I park?" scores some weak durable fact just as highly as
the parking note, and only the model can say which answers the question.

The semantic half is what bridges a synonym - "what do I like to eat" reaches
"likes bagels" through the embedding, which substring matching never could.
The log half covers what Persona structurally cannot: situational notes, which
are never promoted, and anything saved before Persona existed.

WHY RECALL RETURNS MORE THAN IT WAS ASKED FOR
Both halves hand back more than the closest hit. Every match above a low floor
is kept, and the substring narrowing returns recent notes anyway when nothing
matches. Filtering is an optimisation here, not a gate - the model picks.

DEGRADING WITHOUT PERSONA
Persona needs Supabase *and* a local embedding model (fastembed, ~1.2 GB on
first use). Neither is required for the tool to work: a save that cannot reach
Persona still lands in episodic_memory, and a recall that cannot reach Persona
falls back to the substring scan over that same log. Notes saved before
Persona existed are only in the log, and the fallback is what finds them.
"""

from typing import Any, Optional

from .base import BaseTool

# tools/ is imported both as `app.tools` (scripts/tests) and bare `tools` (the
# server, launched as uvicorn main:app from backend/app/). See registry.py.
try:
    from .. import memory                   # app.tools.memory_tool
    from .. import persona
except ImportError:                         # pragma: no cover
    import memory                           # tools.memory_tool (cwd = backend/app)
    import persona

# Notes share episodic_memory with events; this is their event_type discriminator.
NOTE_EVENT_TYPE = "note"

# How many notes a recall hands back, newest first. A prompt-size cap: every
# note handed back goes to the model as text.
RECALL_LIMIT = 20

# How far back the log fallback scans for a substring match. Wider than
# RECALL_LIMIT because it filters before it truncates - a query matching one
# note in the last two hundred should still find it - but bounded, because the
# durable half of recall is Persona's job and this tier is only for notes too
# recent or too situational to have been promoted there.
NOTE_SCAN = 200

# Shortest query word worth matching on - see _matches().
MIN_QUERY_WORD = 3

# Floor for semantic hits - a noise-drop, not a relevance test. Measured
# against the real store, relevant and irrelevant queries overlap (a genuine
# hit at 0.442, an unrelated one at 0.492), so no threshold separates them and
# anything above ~0.45 starts silently returning nothing. Keep it low, return
# both tiers, and let the model choose. See loop.py's PERSONA_MIN_SIMILARITY
# for the numbers.
RECALL_MIN_SIMILARITY = 0.35

# How close a new note's embedding has to be to an existing belief in the SAME
# category before it is treated as an update to that belief rather than a new
# one - the fix for "likes bagels" and "dislikes bagels" otherwise landing as
# two facts that both surface on every recall forever (see persona/__init__.py
# module docstring's "correction loop" example). This is a different regime
# from RECALL_MIN_SIMILARITY above: that floor separates a relevant *question*
# from an irrelevant one, where the two bands measurably overlap (see
# loop.py's PERSONA_MIN_SIMILARITY). Here both sides are short third-person
# statements about the same narrow topic - "likes bagels" vs "dislikes
# bagels" - which share nearly all their wording regardless of polarity, so
# the same-subject case should score well above that noise floor. Unmeasured
# against the real embedder; tune once real saves give a distribution, the
# same way PERSONA_MIN_SIMILARITY's was.
CONTRADICTION_MIN_SIMILARITY = 0.8


class MemoryTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="memory",
            description=(
                "The user's personal notebook - things they have explicitly "
                "asked Nova to remember. Two actions. "
                "Use action 'save' whenever the user asks you to remember, "
                "note, jot down, or keep track of something ('remember I "
                "parked on level 3', 'note that the draft is due Friday'), "
                "and put what they want remembered in `text`, written as a "
                "statement about them. "
                "Use action 'recall' whenever they ask what they noted, or "
                "ask about something they may have told you earlier ('where "
                "did I park?', 'what did I say about the draft?', 'what have "
                "I asked you to remember?'). Pass `query` to narrow the "
                "search, but expect to skim the notes you get back and pick "
                "the relevant one yourself. Call recall before telling the "
                "user you don't know something about them."
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
                            "'save' = write a new note, 'recall' = read notes back."
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
                            "knows about the user, general to specific. Use it "
                            "when the note says something durable about who they "
                            "are - a preference, an opinion, a routine, a course "
                            "they take - e.g. ['opinions', 'likes', 'food'] for "
                            "'likes bagels', or ['facts', 'courses'] for 'studies "
                            "mechanical engineering'. Omit it for anything "
                            "situational or one-off ('parked on level 3', "
                            "'meeting moved to 3pm') - those are kept as "
                            "short-term notes and are not remembered as part "
                            "of who the user is."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Optional for 'recall'. What to look for, in the "
                            "user's own words - it is matched by meaning, not by "
                            "keyword, so a plain question works better than a "
                            "guess at the wording of the note. Omit it to get the "
                            "most recent notes."
                        ),
                    },
                },
                "required": ["action"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        action = tool_input.get("action", "recall")

        if action == "save":
            return _save(
                text=str(tool_input.get("text") or "").strip(),
                tags=[str(t) for t in (tool_input.get("tags") or [])],
                category=[str(c) for c in (tool_input.get("category") or [])],
            )

        if action == "recall":
            return _recall(query=str(tool_input.get("query") or "").strip())

        return {
            "success": False,
            "spoken": "I didn't understand that memory action.",
        }


# --- actions -----------------------------------------------------------------

def _save(text: str, tags: list[str], category: list[str]) -> dict[str, Any]:
    """Append one note to the Memory log (memory.append), then mirror it into
    the Persona vector store so it can be recalled by meaning."""
    if not text:
        return {
            "success": False,
            "spoken": "I'm not sure what you'd like me to remember.",
        }

    record = {
        "event_type": NOTE_EVENT_TYPE,
        "event": {"kind": "note", "text": text, "tags": tags, "category": category},
    }

    # Non-fatal, as everywhere else Memory is touched: an unconfigured or
    # unreachable Supabase must not take the whole turn down - but unlike a
    # background write, the user asked for this, so say it didn't land.
    try:
        note_id = memory.append(record)
    except Exception as e:
        print(f"[memory tool] save failed: {e}")
        return {
            "success": False,
            "spoken": "I couldn't save that just now - my notes aren't reachable.",
        }

    print(f"[memory tool] saved note {note_id}: {text!r}")
    return {
        "success": True,
        "note_id": note_id,
        "note": text,
        "indexed": _promote_to_persona(text, tags, category, note_id),
        "spoken": "Noted.",
    }


def _promote_to_persona(
    text: str, tags: list[str], category: list[str], note_id: str
) -> bool:
    """Mirror a durable note into Persona (Section 5.4) as a searchable belief.

    NOT every note. Persona is the long-term store - who the user is - and
    "parked on level 3" is not who anyone is. It is true for an hour and
    meaningless the moment they drive away, and a fact like that in a store
    searched by meaning is worse than absent: it surfaces forever, competing
    with the things that do matter. Situational notes stay in episodic memory,
    which is where recall's fallback still finds them while they are current.

    `category` is the test, and the model supplies it: it files a note into the
    ontology only when the note says something durable about the user (the tool
    schema spells out the distinction). No category means situational.

    Before writing, checks whether this note updates a belief already sitting
    in the same category (e.g. "likes bagels" -> "dislikes bagels") and, if so,
    re-upserts with that fact's id rather than inserting a new one - otherwise
    both wind up in Persona permanently, competing on every future recall. See
    _find_contradicted().

    Returns whether it landed. Best-effort by design: Persona needs Supabase
    *and* the embedding model, and the note is already safely in the log by the
    time we get here, so a failure downgrades recall from semantic to substring
    rather than losing anything. The user is not told - from their side the
    note was saved, which it was.
    """
    if not category:
        print(f"[memory tool] note {note_id} kept episodic (no category - situational)")
        return False

    existing_id = _find_contradicted(text, category)

    try:
        fact_id = persona.upsert(persona.Fact(
            id=existing_id,
            text=text,
            category=category,
            # source "stated" is the default the Intent Surface assumes, and it
            # is what makes this outrank a fact consolidation merely inferred.
            metadata={"source": "stated", "note_id": note_id, "tags": tags},
        ))
    except Exception as e:
        print(f"[memory tool] persona upsert skipped: {e}")
        return False

    if existing_id:
        print(f"[memory tool] note {note_id} updated persona fact {fact_id} (was {existing_id!r})")
    else:
        print(f"[memory tool] indexed note {note_id} as persona fact {fact_id}")
    return True


def _find_contradicted(text: str, category: list[str]) -> Optional[str]:
    """The id of an existing belief this note supersedes, or None if this is a
    new one.

    Scoped to the same category path (a prefix match - see
    db/schema.sql's match_persona) so "likes bagels" is only ever compared
    against other food opinions, never against an unrelated belief that
    happens to embed similarly. Above CONTRADICTION_MIN_SIMILARITY within that
    scope is treated as the same belief restated, whether or not the polarity
    changed - upsert() overwrites its text either way.

    Best-effort like everything else in this file: a search failure here must
    not block the save, so it is treated the same as no match and the note
    lands as a new fact rather than being lost.
    """
    try:
        matches = persona.search(persona.PersonaQuery(
            text=text,
            category=category,
            limit=1,
            min_similarity=CONTRADICTION_MIN_SIMILARITY,
        ))
    except Exception as e:
        print(f"[memory tool] contradiction search skipped: {e}")
        return None

    return matches[0].fact.id if matches else None


def _recall(query: str) -> dict[str, Any]:
    """Both tiers at once: durable facts by meaning, plus recent notes.

    NOT either/or. Similarity cannot tell a relevant fact from an irrelevant
    one here - measured, the bands overlap (see loop.py's
    PERSONA_MIN_SIMILARITY) - so a persona hit is never good enough evidence to
    conclude the log has nothing better. "Where did I park?" returns some weak
    durable fact and the parking note; only the model can say which answers the
    question, so it gets both, each labelled with where it came from.

    The log half also covers what Persona structurally cannot: situational
    notes, which are never promoted, and anything saved before Persona existed.
    """
    facts = _search_persona(query) if query else []
    notes = _notes_from_log(query)
    if notes is None:                      # the log itself is unreachable
        return _recalled(facts, source="persona") if facts else _unreachable()

    seen = {f["text"] for f in facts}
    merged = facts + [n for n in notes if n["text"] not in seen]
    return _recalled(merged[:RECALL_LIMIT], source=_source_of(facts, notes))


def _source_of(facts: list[dict[str, Any]], notes: list[dict[str, Any]]) -> str:
    if facts and notes:
        return "persona+log"
    return "persona" if facts else "log"


def _unreachable() -> dict[str, Any]:
    return {"success": False, "spoken": "I couldn't check my notes just now."}


def _search_persona(query: str) -> list[dict[str, Any]]:
    """Semantic hits for `query`, best first. [] if Persona is unreachable -
    the caller falls back to the log rather than reporting a failure."""
    try:
        matches = persona.search(persona.PersonaQuery(
            text=query,
            limit=RECALL_LIMIT,
            min_similarity=RECALL_MIN_SIMILARITY,
        ))
    except Exception as e:
        print(f"[memory tool] persona search skipped: {e}")
        return []

    print(f"[memory tool] recall query={query!r}: {len(matches)} semantic matches")
    return [
        {
            # isoformat, not the datetime the store hands back: the log path
            # returns strings and both shapes end up in the same prompt.
            "created_at": m.fact.created_at.isoformat() if m.fact.created_at else None,
            "text": m.fact.text,
            "tags": (m.fact.metadata or {}).get("tags") or [],
            "category": m.fact.category,
            "similarity": round(m.similarity, 3),
        }
        for m in matches
    ]


def _notes_from_log(query: str) -> list[dict[str, Any]] | None:
    """Notes from the Memory log, newest first, narrowed by substring when that
    leaves anything. None means the log itself could not be read - which is
    different from it being empty, and the caller treats it differently.

    Scans a window rather than the whole log. This is the fallback tier: the
    substring match only ever finds a note the user phrases the same way twice,
    and anything older than the window that is genuinely about them should have
    been promoted to Persona, where the search above finds it by meaning.
    """
    try:
        rows = memory.recent(NOTE_EVENT_TYPE, NOTE_SCAN)    # oldest first
    except Exception as e:
        print(f"[memory tool] log read failed: {e}")
        return None

    notes = [_as_note(r) for r in reversed(rows)]           # newest first

    if query:
        matched = [n for n in notes if _matches(n, query)]
        print(f"[memory tool] recall query={query!r}: {len(matched)}/{len(notes)} matched in log")
        if matched:
            notes = matched

    return notes[:RECALL_LIMIT]


def _recalled(notes: list[dict[str, Any]], source: str) -> dict[str, Any]:
    """One result shape however the hits were found. Durable facts come first
    (ranked by meaning), then recent notes - each carries `similarity` or not,
    so the model can tell which tier it is reading."""
    if not notes:
        spoken = "I haven't got any notes saved yet."
    elif len(notes) == 1:
        spoken = f"One note: {notes[0]['text']}"
    else:
        ordering = "most relevant first" if source.startswith("persona") else "most recent first"
        spoken = f"{len(notes)} notes, {ordering}: " + "; ".join(
            n["text"] for n in notes[:3]
        )
        if len(notes) > 3:
            spoken += f" and {len(notes) - 3} more"

    return {
        "success": True,
        "count": len(notes),
        "notes": notes,
        "matched_by": source,
        "spoken": spoken,
    }


# --- row <-> note ------------------------------------------------------------

def _as_note(row: dict[str, Any]) -> dict[str, Any]:
    """The note fields out of an episodic_memory row."""
    event = row.get("event") or {}
    return {
        "created_at": row.get("created_at"),
        "text": event.get("text", ""),
        "tags": event.get("tags") or [],
        "category": event.get("category") or [],
    }


def _matches(note: dict[str, Any], query: str) -> bool:
    """True if any word of `query` appears in the note's text or tags.

    Words shorter than MIN_QUERY_WORD are dropped: matching is by substring, so
    an "I" or an "a" left in the query would hit every note and quietly turn
    narrowing off. A query of nothing but short words matches nothing, which
    _recall() then falls back out of.
    """
    haystack = (note["text"] + " " + " ".join(note["tags"])).lower()
    words = [w for w in query.lower().split() if len(w) >= MIN_QUERY_WORD]
    return any(word in haystack for word in words)
