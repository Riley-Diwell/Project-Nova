"""The standardised scheme for a derived user fact.

A `Candidate` is a repetition found in the episodic log by counting. A
`DerivedFact` is that repetition once it has been phrased and filed into the
ontology, ready to become a persona.Fact. The split matters: everything in
Candidate is arithmetic over rows and can be recomputed exactly, everything
added by DerivedFact is the model's wording. Keeping them apart is what makes
a derived belief auditable - you can always ask which episodes produced it.

THE ONTOLOGY
Derived facts are filed under a small fixed set of top-level paths so that a
category-filtered search means something. Consolidation only ever writes these;
anything richer is a level deeper.

    ["routines", "places"]    - somewhere the user repeatedly goes
    ["routines", "timing"]    - when they repeatedly do something
    ["routines", "apps"]      - what they repeatedly interact with
    ["preferences", <domain>] - a choice made consistently when alternatives existed

EVIDENCE
Every derived fact carries its own provenance in `metadata`, which is what
distinguishes it from a fact the user simply stated:

    {"source": "derived", "support": 5, "span_days": 5,
     "first_seen": ..., "last_seen": ..., "episode_ids": [...],
     "signal": "navigation_departure_time:destination"}

`support` is the number of episodes behind the claim. It is also what sets
`confidence`, so a belief held on two sightings is visibly weaker than one held
on twenty - see confidence_for().
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

# Ontology roots. Consolidation writes only these; see the module docstring.
CATEGORY_ROUTINE_PLACES = ["routines", "places"]
CATEGORY_ROUTINE_TIMING = ["routines", "timing"]
CATEGORY_ROUTINE_APPS = ["routines", "apps"]
CATEGORY_PREFERENCES = ["preferences"]

# Marks a fact as inferred rather than stated. The Intent Surface is told to
# prefer what the user said over what NOVA worked out, and the Knowledge Map
# needs to show the difference, so it has to survive into the store.
SOURCE_DERIVED = "derived"

# The other provenance, and the reason the two passes are separate. A stated
# fact needs no support count - the user said it once, outright, and that
# settles it. A derived fact is an inference from repetition and has to earn
# its confidence. Same store, same search, different standard of evidence.
SOURCE_STATED = "stated"

# Fewest episodes before a repetition is a trend at all. Two of anything is a
# coincidence; three is the first point a habit is worth asserting to the user.
MIN_SUPPORT = 3

# Support at which confidence saturates. Beyond this more sightings do not make
# the belief meaningfully truer, they just make it older.
FULL_SUPPORT = 8


def confidence_for(support: int) -> float:
    """Confidence from evidence count, linear from MIN_SUPPORT to FULL_SUPPORT.

    Deliberately crude and deliberately not the model's opinion: the number
    means "how much did we see", which is checkable, rather than "how sure is
    Claude", which is not. Floors at 0.5 so a fact that cleared MIN_SUPPORT is
    never stored as barely-believed.
    """
    if support <= MIN_SUPPORT:
        return 0.5
    if support >= FULL_SUPPORT:
        return 1.0
    span = FULL_SUPPORT - MIN_SUPPORT
    return round(0.5 + 0.5 * (support - MIN_SUPPORT) / span, 3)


class Candidate(BaseModel):
    """A repetition found by counting, before anything interprets it.

    `signal` names what was counted, as "<tool>:<field>" (e.g.
    "navigation_departure_time:destination"), and `value` is the recurring
    value of that field. `exemplars` carries a few of the raw user phrasings so
    the phrasing pass can see how the user talks about this, not just the
    resolved entity.
    """

    signal: str
    value: str
    support: int
    span_days: float
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    episode_ids: list[str] = Field(default_factory=list)
    exemplars: list[str] = Field(default_factory=list)
    # Distribution of the hour-of-day the episodes landed in, when known - the
    # difference between "goes to the bagel shop" and "goes to the bagel shop
    # on weekday mornings".
    hours: list[int] = Field(default_factory=list)

    def evidence(self) -> dict[str, Any]:
        """The provenance block written to persona.Fact.metadata."""
        return {
            "source": SOURCE_DERIVED,
            "signal": self.signal,
            "value": self.value,
            "support": self.support,
            "span_days": self.span_days,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "episode_ids": self.episode_ids,
        }


class DerivedFact(BaseModel):
    """A Candidate phrased and filed, ready to upsert into Persona."""

    text: str
    category: list[str]
    candidate: Candidate

    @property
    def confidence(self) -> float:
        return confidence_for(self.candidate.support)


class StatedFact(BaseModel):
    """Something the user said about themselves, ready to upsert into Persona.

    Deliberately NOT a Candidate: there is nothing to count. "I like bagels"
    said once is a fact about the user; five navigation requests to a bagel
    shop is an inference that happens to be about the same thing. Keeping the
    two apart is what lets the Intent Surface prefer the first over the second
    when they disagree.

    `quote` is the user's original wording, kept so a belief can be traced back
    to the sentence that produced it - the stated-fact equivalent of
    Candidate.episode_ids.
    """

    text: str
    category: list[str] = Field(default_factory=lambda: ["facts"])
    quote: str = ""
    episode_id: Optional[str] = None

    # Other episodes that said the same thing, folded in by deduplication. One
    # fact routinely appears twice in the log - once as the voice event, once
    # as the note the memory tool saved from it - and both ids are kept so the
    # merge stays auditable rather than silently dropping an episode.
    also_from: list[str] = Field(default_factory=list)

    # Said outright, so there is no evidence to weigh. Contrast confidence_for().
    confidence: float = 1.0

    def evidence(self) -> dict[str, Any]:
        """The provenance block written to persona.Fact.metadata."""
        return {
            "source": SOURCE_STATED,
            "quote": self.quote,
            "episode_id": self.episode_id,
            "also_from": self.also_from,
        }
