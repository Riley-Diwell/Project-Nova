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
source "derived". Where the two disagree, the Intent Surface prefers what was
said - which only works because the provenance is stored, not guessed.

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
from typing import Any, Callable, Iterable, Optional

from .models import StatedFact

# Episode kinds whose text is the user talking. Notes are included: a note is a
# statement the user dictated, and one saved without a category never got
# promoted at save time (tools/memory_tool.py keeps those episodic), so this is
# the pass that gives it a second look.
SPOKEN_EVENT_TYPES = ("voice", "note")

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
        text = ((row.get("event") or {}).get("text") or "").strip()
        if len(text) < MIN_UTTERANCE:
            continue
        out.append({"id": episode_id, "text": text})
    return out


def find_statements(
    rows: list[dict[str, Any]],
    seen_episode_ids: Iterable[str] = (),
    extractor: Optional[Extractor] = None,
    existing_texts: Iterable[str] = (),
) -> list[StatedFact]:
    """Every durable self-statement in `rows` not already held.

    Two kinds of duplicate have to be removed, and skipping either one fills
    Persona with near-identical beliefs that all surface on every search:

      - within this run. One statement usually produces TWO episodes - the
        voice event where the user said it, and the note the memory tool saved
        in the same turn. Both extract to the same fact.
      - against the store. A note promoted at save time is already in Persona,
        so re-reading the utterance it came from would add it a second time.
    """
    pending = utterances(rows, seen_episode_ids)
    print(f"[statements] {len(pending)} unextracted utterance(s)")
    if not pending:
        return []

    extract = extractor or _claude_extractor
    facts: list[StatedFact] = []
    for start in range(0, len(pending), BATCH):
        facts.extend(extract(pending[start:start + BATCH]))

    by_id = {u["id"]: u["text"] for u in pending}
    held = {_norm_fact(t) for t in existing_texts}
    merged: dict[str, StatedFact] = {}

    for fact in facts:
        # The extractor may only speak about utterances it was given.
        if fact.episode_id not in by_id:
            print(f"[statements] dropped fact for unknown episode: {fact.text!r}")
            continue
        if not fact.text.strip():
            continue

        key = _norm_fact(fact.text)
        if key in held:
            print(f"[statements] already held, skipping: {fact.text!r}")
            continue
        if key in merged:
            merged[key].also_from.append(fact.episode_id)
            continue

        fact.quote = fact.quote or by_id[fact.episode_id]
        merged[key] = fact

    return list(merged.values())


def _norm_fact(text: str) -> str:
    """Fact identity for deduplication - wording that differs only in case,
    spacing or a trailing full stop is the same belief."""
    return " ".join(str(text).lower().strip().rstrip(".").split())


def _claude_extractor(batch: list[dict[str, Any]]) -> list[StatedFact]:
    from anthropic import Anthropic

    from . import MODEL, _parse_json_array

    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=EXTRACTION_PROMPT,
        messages=[{"role": "user", "content": json.dumps(batch)}],
    )
    text = "".join(b.text for b in response.content if b.type == "text")

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
