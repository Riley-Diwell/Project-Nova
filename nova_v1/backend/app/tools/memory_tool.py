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

Both halves are the generic helpers in app/memory/__init__.py: a save is
memory.write(), a recall is memory.read(). Notes live in the same
episodic_memory table as events, under event_type "note", so they need no
schema of their own (backend/db/schema.sql is unchanged) and the whole log
stays readable through one pair of functions.

WHY RECALL RETURNS MORE THAN IT WAS ASKED FOR
`query` narrows by substring, but when it matches nothing this returns the
recent notes anyway rather than an empty list. Substring matching cannot bridge
a synonym - "what do I like to eat" never matches a note saying "bagels" - so
the model, which can bridge it, is handed the notes and does the matching
itself. Filtering is an optimisation here, not a gate.
"""

from typing import Any

from .base import BaseTool

# tools/ is imported both as `app.tools` (scripts/tests) and bare `tools` (the
# server, launched as uvicorn main:app from backend/app/). See registry.py.
try:
    from .. import memory                   # app.tools.memory_tool
except ImportError:                         # pragma: no cover
    import memory                           # tools.memory_tool (cwd = backend/app)

# Notes share episodic_memory with events; this is their event_type discriminator.
NOTE_EVENT_TYPE = "note"

# How many notes a recall hands back, newest first. A prompt-size cap - read()
# returns the whole log, and every note goes to the model as text.
RECALL_LIMIT = 20

# Shortest query word worth matching on - see _matches().
MIN_QUERY_WORD = 3


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
                    "query": {
                        "type": "string",
                        "description": (
                            "Optional for 'recall'. Words to narrow the notes by. "
                            "Omit it to get the most recent notes."
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
                tags=tool_input.get("tags") or [],
            )

        if action == "recall":
            return _recall(query=str(tool_input.get("query") or "").strip())

        return {
            "success": False,
            "spoken": "I didn't understand that memory action.",
        }


# --- actions -----------------------------------------------------------------

def _save(text: str, tags: list[str]) -> dict[str, Any]:
    """Append one note to the Memory log (memory.write)."""
    if not text:
        return {
            "success": False,
            "spoken": "I'm not sure what you'd like me to remember.",
        }

    record = {
        "event_type": NOTE_EVENT_TYPE,
        "event": {"kind": "note", "text": text, "tags": [str(t) for t in tags]},
    }

    # Non-fatal, as everywhere else Memory is touched: an unconfigured or
    # unreachable Supabase must not take the whole turn down - but unlike a
    # background write, the user asked for this, so say it didn't land.
    try:
        note_id = memory.write(record)
    except Exception as e:
        print(f"[memory tool] save failed: {e}")
        return {
            "success": False,
            "spoken": "I couldn't save that just now - my notes aren't reachable.",
        }

    print(f"[memory tool] saved note {note_id}: {text!r}")
    return {"success": True, "note_id": note_id, "note": text, "spoken": "Noted."}


def _recall(query: str) -> dict[str, Any]:
    """Read notes back (memory.read), newest first, narrowed by `query` when
    that leaves anything - see the module docstring on why it isn't a gate."""
    try:
        rows = memory.read("event_type", NOTE_EVENT_TYPE)   # oldest first
    except Exception as e:
        print(f"[memory tool] recall failed: {e}")
        return {
            "success": False,
            "spoken": "I couldn't check my notes just now.",
        }

    notes = [_as_note(r) for r in reversed(rows)]           # newest first

    if query:
        matched = [n for n in notes if _matches(n, query)]
        print(f"[memory tool] recall query={query!r}: {len(matched)}/{len(notes)} matched")
        if matched:
            notes = matched

    notes = notes[:RECALL_LIMIT]

    if not notes:
        spoken = "I haven't got any notes saved yet."
    elif len(notes) == 1:
        spoken = f"One note: {notes[0]['text']}"
    else:
        spoken = f"{len(notes)} notes, most recent first: " + "; ".join(
            n["text"] for n in notes[:3]
        )
        if len(notes) > 3:
            spoken += f" and {len(notes) - 3} more"

    return {"success": True, "count": len(notes), "notes": notes, "spoken": spoken}


# --- row <-> note ------------------------------------------------------------

def _as_note(row: dict[str, Any]) -> dict[str, Any]:
    """The note fields out of an episodic_memory row."""
    event = row.get("event") or {}
    return {
        "created_at": row.get("created_at"),
        "text": event.get("text", ""),
        "tags": event.get("tags") or [],
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
