"""Data structures for the Persona store.

A Persona holds the durable, slow-changing model of *who the user is*
(opinions -> likes -> food -> [bagels]). Vector-searchable. NO raw sensor data.

EVERYTHING HERE IS DURABLE. That is the store's defining property, not a
convention: Persona is searched by meaning and never expires, so anything
situational put here would surface forever. "Parked on level 3" belongs in
episodic memory and stays there (see tools/memory_tool.py).

Entries arrive two ways, told apart by `metadata["source"]`:

  * "stated"  - the user said it, and tools/memory_tool.py promoted it because
    the note was filed into the ontology rather than left situational.
    `metadata` carries the episodic_memory row it came from.
  * "derived" - app/consolidation counted it out of repeated behaviour, e.g.
    five navigation requests that all resolved to the same bagel shop.
    `metadata` carries the evidence: support, span, and the episode ids.

Both are searched by the same embedding, so one query reaches both. Where they
disagree, "stated" wins - the Intent Surface is told as much, which is why the
provenance has to be stored rather than inferred.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class Fact(BaseModel):
    """One durable belief about the user.

    `text` is the grounded statement (indexicals already dereferenced before
    upsert, for grounding). `category` is the hierarchy path into the ontology,
    e.g. ["opinions", "likes", "food"] for a belief or ["notes"] for something
    the user dictated.
    """

    text: str
    category: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Set by the store; do not populate by hand.
    id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def tombstone_key(metadata: dict[str, Any]) -> Optional[str]:
    """The identity of the pattern behind a Fact, for the forgotten list.

    Deleting a belief has to outlive the row, or consolidation re-derives it on
    its next run and the user's correction is silently undone. What survives is
    only this key - never the belief text, which is the whole point: the store
    forgets what it believed while remembering that it was told to stop.

    A derived fact is identified by the repetition it was counted from
    (signal, value); a stated one by the utterance it was extracted from, or
    the note it was promoted from. Facts with none of those - anything the user
    typed straight into the Knowledge Map - have nothing that would regenerate
    them, so they need no tombstone.

    The primary key only; see tombstone_keys() for everything a delete records.
    """
    keys = tombstone_keys(metadata)
    return keys[0] if keys else None


def tombstone_keys(metadata: dict[str, Any]) -> list[str]:
    """Every pattern a Fact could be regenerated from, most specific first.

    A fact promoted from a note can carry both an `episode_id` (the turn the
    user asked in) and a `note_id` (the note it was saved as), and either one
    reaching consolidation again would bring it back - so a delete has to
    remember both. That was the old bug: a promoted fact carried only
    `note_id`, which nothing recognised, so no tombstone was ever written.
    """
    keys: list[str] = []
    signal, value = metadata.get("signal"), metadata.get("value")
    if signal and value:
        keys.append(f"trend:{signal}:{value}")
    episode_id = metadata.get("episode_id")
    if episode_id:
        keys.append(f"episode:{episode_id}")
    note_id = metadata.get("note_id")
    if note_id:
        keys.append(note_key(str(note_id)))
    question_id = metadata.get("question_id")
    if metadata.get("origin") == "onboarding" and question_id:
        keys.append(onboarding_key(str(question_id)))
    return keys


def note_key(note_id: str) -> str:
    """The tombstone key for a note - see store/notes."""
    return f"note:{note_id}"


def onboarding_key(question_id: str) -> str:
    """The tombstone key for an onboarding answer's fact - see store/profile.
    Deleting one stops the same answer re-seeding it; changing the answer does."""
    return f"onboarding:{question_id}"


class Match(BaseModel):
    """A Fact returned from semantic search, with its similarity score."""

    fact: Fact
    similarity: float  # cosine similarity in [0, 1]; higher is closer


class PersonaQuery(BaseModel):
    """Read filter for `persona.search`."""

    text: str                             # natural-language query, embedded then matched
    category: Optional[list[str]] = None  # restrict to a subtree of the ontology
    limit: int = 5
    min_similarity: float = 0.0           # drop weak matches below this score
