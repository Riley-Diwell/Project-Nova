"""Memory -> Persona correction loop (Build Plan section 5.5, done-when b).

An episodic Memory entry can carry new durable information that contradicts a
stored belief ("this bagel is good, I was wrong" -> find the bagel opinion ->
update it). This module reconciles that: it reads episodes, searches Persona for
the affected belief, and updates it in place.

Design (per the agreed recommendations):
  * OUT OF BAND. `run_reflection_pass()` is meant to run off the request path
    (a background worker / scheduled job), never inside POST /event - the voice
    interaction must stay low-latency (Attention pillar).
  * SWAPPABLE JUDGEMENT. The two decisions that really need an LLM - "is there a
    grounded durable claim in this episode?" and "does it contradict this
    belief?" - live behind the `Reflector` protocol. `StubReflector` is an
    LLM-free stand-in so the wiring + tests land now; Georgia's Intent Surface
    can drop in a Claude-backed `Reflector` later with no change here.
  * IDEMPOTENT. Memory is append-only, so a reconciled episode is marked
    (`outcome.detail["reflected"] = True`) and skipped on the next pass.

This module consumes only the two frozen store seams (sections 5.4 / 5.5); it
adds no new seam of its own.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .memory import (
    MemoryEntry,
    MemoryFilter,
    Outcome,
    query as memory_query,
    update_outcome,
)
from .persona import (
    Fact,
    PersonaQuery,
    search as persona_search,
    upsert as persona_upsert,
)

# A candidate belief must be at least this close to the claim to be treated as
# the thing being corrected. Tuned for bge cosine similarity (contradicting
# rephrasings like "likes X" / "dislikes X" score high; unrelated beliefs low).
DEFAULT_MIN_SIMILARITY = 0.5


class DurableClaim(BaseModel):
    """A grounded, durable statement extracted from an episode, ready for Persona.

    Grounding (indexical dereferencing) is assumed already done upstream - an
    ungrounded reference must never produce a claim (it stays episodic-only).
    """

    text: str
    category: list[str] = Field(default_factory=list)
    confidence: float = 1.0


class ReflectionResult(BaseModel):
    """What one episode's reconciliation did."""

    entry_id: Optional[str]
    action: str  # "noop" | "created" | "updated"
    fact_id: Optional[str] = None
    claim_text: Optional[str] = None


@runtime_checkable
class Reflector(Protocol):
    """The LLM-shaped decisions, isolated for a Claude-backed impl later."""

    def extract_claim(self, entry: MemoryEntry) -> Optional[DurableClaim]:
        """A grounded durable claim in this episode, or None."""
        ...

    def contradicts(self, claim: DurableClaim, fact: Fact) -> bool:
        """True if `claim` should overwrite the existing `fact`."""
        ...


class StubReflector:
    """LLM-free stand-in.

    * `extract_claim` reads a pre-grounded structured claim off the episode
      (`entry.event["durable_claim"]`). A Claude-backed reflector would instead
      read `entry.event["text"]` and derive + ground the claim itself.
    * `contradicts` treats any sufficiently-similar belief in the same ontology
      subtree as the correction target. The real reflector makes the nuanced
      call (contradiction vs mere refinement).
    """

    def extract_claim(self, entry: MemoryEntry) -> Optional[DurableClaim]:
        raw = (entry.event or {}).get("durable_claim")
        if not raw:
            return None
        return DurableClaim(**raw)

    def contradicts(self, claim: DurableClaim, fact: Fact) -> bool:
        return True


def reconcile_episode(
    entry: MemoryEntry,
    reflector: Reflector,
    *,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> ReflectionResult:
    """Reconcile a single episode against Persona. Marks the episode reflected."""
    claim = reflector.extract_claim(entry)
    if claim is None:
        _mark_reflected(entry)
        return ReflectionResult(entry_id=entry.id, action="noop")

    # 1. what does Persona already believe on this topic?
    hits = persona_search(
        PersonaQuery(
            text=claim.text,
            category=claim.category or None,
            min_similarity=min_similarity,
            limit=5,
        )
    )
    # 2. is any of those the belief this claim overturns?
    target = next((h for h in hits if reflector.contradicts(claim, h.fact)), None)

    # 3. update in place, or add a net-new belief
    if target is None:
        fact_id = persona_upsert(
            Fact(text=claim.text, category=claim.category, confidence=claim.confidence)
        )
        action = "created"
    else:
        fact_id = persona_upsert(
            Fact(
                id=target.fact.id,
                text=claim.text,
                category=target.fact.category,
                confidence=_blend(target.fact.confidence, claim.confidence),
            )
        )
        action = "updated"

    _mark_reflected(entry)
    return ReflectionResult(
        entry_id=entry.id, action=action, fact_id=fact_id, claim_text=claim.text
    )


def run_reflection_pass(
    reflector: Optional[Reflector] = None,
    *,
    since: Optional[datetime] = None,
    limit: int = 200,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> list[ReflectionResult]:
    """Reconcile every not-yet-reflected episode. Run this out of band."""
    reflector = reflector or StubReflector()
    results: list[ReflectionResult] = []
    for entry in memory_query(MemoryFilter(since=since, limit=limit)):
        if _already_reflected(entry):
            continue
        results.append(
            reconcile_episode(entry, reflector, min_similarity=min_similarity)
        )
    return results


def _blend(old: float, new: float) -> float:
    """Move confidence toward the new observation. Simple + tunable; a richer
    accrual policy (require repetition to overturn a strong belief) slots in here."""
    return round(0.5 * old + 0.5 * new, 4)


def _already_reflected(entry: MemoryEntry) -> bool:
    return bool(entry.outcome and entry.outcome.detail.get("reflected"))


def _mark_reflected(entry: MemoryEntry) -> None:
    """Stamp the episode so the next pass skips it. No-op if it has no id."""
    if not entry.id:
        return
    outcome = entry.outcome or Outcome(status="reflected")
    detail = dict(outcome.detail)
    detail["reflected"] = True
    update_outcome(entry.id, outcome.model_copy(update={"detail": detail}))
