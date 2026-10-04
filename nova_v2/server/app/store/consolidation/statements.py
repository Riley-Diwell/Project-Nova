"""Pulling facts the user *stated* out of the episodic log.

The other half of consolidation, and deliberately not the same machinery.
trends.py answers "what does this user keep doing?" by counting resolved tool
parameters. This answers "what has this user told me about themselves?" by
reading what they actually said.

WHY IT IS SEPARATE
Different evidence, different standard. A trend needs repetition before it
means anything - one navigation request is not a habit. A statement needs none:
"I like bagels", said once, in passing, is a fact about the user the moment it
leaves their mouth. Counting it would be the wrong test entirely, and holding
it to MIN_SUPPORT would throw away exactly the thing the user told you.

They also land differently. A stated fact carries confidence 1.0 and
source "stated"; a derived one earns its confidence from support and carries
source "derived". Where any two beliefs disagree, persona.remember() keeps the
more recent - so every fact here carries when it was SAID (the episode's
time), not when this pass happened to read it.

WHAT COUNTS AS A STATEMENT
Durable and about the user. The log mixes all of these together:

    "I like dinosaur nuggets"          -> yes, a preference
    "I study mechanical engineering"   -> yes, an attribute
    "I am parked on level 3"           -> NO, situational, true for an hour
    "take me to the bagel shop"        -> NO, a request, not a statement
    "what food do I like"              -> NO, a question

The last three are the reason this is an LLM pass and not a regex. "I am parked
on level 3" and "I like dinosaur nuggets" are the same grammatical shape; only
meaning separates them, and putting the first in a store searched by meaning
and never expiring would be a bug that surfaces forever.

IDEMPOTENCE
Every fact records the episode it came from. Episodes already represented in
Persona are skipped outright, so re-running neither re-reads them nor pays to
re-phrase them - which matters here, unlike trends, because there is no
(signal, value) identity to match a re-derived statement against.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

from app.store.consolidation.models import StatedFact
from app.store.persona.models import normalise_text
from app.tools.core.action import Action
from app.core.config import said

# Episode kinds whose text is the user talking.
#
# "note" is deliberately NOT here any more. Notes live in their own store
# (app/store/notes) and the user decides what they are for: a lecture capture
# is a lecturer's sentences, not facts about the user, and a verbatim quick
# note is situational by construction. A note only reaches Persona when the
# user explicitly promotes it.
SPOKEN_EVENT_TYPES = ("voice",)

# A voice turn in which the memory tool already saved something has been dealt
# with: whatever the user wanted kept is a note now, promoted or not by their
# own choice. Re-reading the utterance would put it into Persona by the back
# door - and after the user deletes that promoted fact, bring it back.
MEMORY_TOOL = "memory"

# Utterances shorter than this are not worth a phrasing call.
MIN_UTTERANCE = 8

# How many utterances to hand the model at once.
BATCH = 40

Extractor = Callable[[list[dict[str, Any]]], list[StatedFact]]

EXTRACTION_PROMPT = (
    "You are reading things one user said to their assistant, and pulling out "
    "durable facts ABOUT THAT USER for long-term memory.\n\n"
    "Each input has an `id` and the user's exact words in `text`.\n\n"
    "Return a fact ONLY when the user stated something about themselves that "
    "will still be true in six months. Good: preferences, opinions, habits "
    "they describe, where they live or study, who people are to them, "
    "possessions, recurring commitments.\n\n"
    "Return NOTHING for an utterance that is:\n"
    "  - a question ('what food do I like', 'where is my car')\n"
    "  - a request or command ('take me to the bagel shop', 'remind me')\n"
    "  - situational and short-lived - true now, meaningless next week "
    "('I am parked on level 3', 'my boss is at golf today', 'the meeting "
    "moved to 3pm'). These are the most important to exclude: this store is "
    "searched by meaning and never expires, so a fact like that would surface "
    "forever.\n"
    "  - about the world rather than about the user ('the 27 bus runs every "
    "twelve minutes' is only a fact about them if they say they use it).\n\n"
    "Most utterances yield nothing. That is the expected outcome - do not "
    "reach for a fact that is not clearly there.\n\n"
    "For each fact you do return, give an object with:\n"
    "  id       - the id of the utterance it came from\n"
    "  text     - the fact in the third person, standalone, e.g. "
    "'Likes dinosaur nuggets'. No hedging, no 'the user said'.\n"
    "  category - ontology path, general to specific, e.g. "
    "[\"opinions\",\"likes\",\"food\"], [\"facts\",\"courses\"], "
    "[\"facts\",\"people\"].\n\n"
    "Return ONLY a JSON array, no prose. An empty array is a valid answer."
)


def utterances(
    rows: list[dict[str, Any]], seen_episode_ids: Iterable[str] = ()
) -> list[dict[str, Any]]:
    """The user's own words out of the log, minus episodes already extracted."""
    already = set(seen_episode_ids)
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("event_type") not in SPOKEN_EVENT_TYPES:
            continue
        episode_id = str(row.get("id") or "")
        if not episode_id or episode_id in already:
            continue
        if _saved_a_note(row):
            continue
        text = ((row.get("event") or {}).get("text") or "").strip()
        if len(text) < MIN_UTTERANCE:
            continue
        out.append({"id": episode_id, "text": text})
    return out


def _saved_a_note(row: dict[str, Any]) -> bool:
    """True if this turn's memory tool ran a save - see MEMORY_TOOL."""
    action = row.get("action")
    if not isinstance(action, dict):
        return False
    return any(
        a.tool == MEMORY_TOOL and a.ran and (a.input or {}).get("action") == "save"
        for a in Action.from_episode(action)
    )


def find_statements(
    rows: list[dict[str, Any]],
    seen_episode_ids: Iterable[str] = (),
    extractor: Optional[Extractor] = None,
) -> list[StatedFact]:
    """Every durable self-statement in `rows` not yet extracted, each carrying
    when it was said (`stated_at`, the episode's time).

    Exact duplicates within this run are folded together here - one statement
    usually produces TWO episodes, the voice event and the note saved in the
    same turn - keeping the latest time. Duplicates of what is already held
    are NOT skipped here any more: persona.remember() merges them, which also
    moves the held belief's time forward. Skipping them would leave it dated
    by its first mention, and an older contradiction could then win.
    """
    pending = utterances(rows, seen_episode_ids)
    print(f"[statements] {len(pending)} unextracted utterance(s)")
    if not pending:
        return []

    extract = extractor or _model_extractor
    facts: list[StatedFact] = []
    for start in range(0, len(pending), BATCH):
        facts.extend(extract(pending[start:start + BATCH]))

    by_id = {u["id"]: u["text"] for u in pending}
    said_at = {str(r.get("id")): _time(r.get("created_at")) for r in rows}
    merged: dict[str, StatedFact] = {}

    for fact in facts:
        # The extractor may only speak about utterances it was given.
        if fact.episode_id not in by_id:
            print(f"[statements] dropped fact for unknown episode: {said(fact.text)}")
            continue
        if not fact.text.strip():
            continue

        when = said_at.get(fact.episode_id)
        key = normalise_text(fact.text)
        if key in merged:
            held = merged[key]
            held.also_from.append(fact.episode_id)
            if when and (held.stated_at is None or when > held.stated_at):
                held.stated_at = when
            continue

        fact.quote = fact.quote or by_id[fact.episode_id]
        fact.stated_at = when
        merged[key] = fact

    return list(merged.values())


def _time(value: Any) -> Optional[datetime]:
    """An episode's created_at as an aware datetime, or None if unusable."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _model_extractor(batch: list[dict[str, Any]]) -> list[StatedFact]:
    from app.core import llm
    from app.store.consolidation import MODEL, _parse_json_array

    text = llm.complete(EXTRACTION_PROMPT, json.dumps(batch), max_tokens=2048,
                        timeout=120.0, model=MODEL)

    facts: list[StatedFact] = []
    for item in _parse_json_array(text):
        category = item.get("category")
        facts.append(StatedFact(
            text=str(item.get("text") or "").strip(),
            category=[str(c) for c in category] if isinstance(category, list) and category
                     else ["facts"],
            episode_id=str(item.get("id") or ""),
        ))
    return facts
