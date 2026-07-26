"""Tests for the Memory -> Persona correction loop (section 5.5, done-when b).

Runs against in-memory fakes for both stores and the StubReflector, so no cloud
and no embedding model are needed. FakeEmbedder is not semantic, so Persona
search here leans on the category-subtree filter with a zero similarity floor;
the real loop uses bge similarity to find the affected belief.
"""
import pytest

from app import memory, persona
from app.memory import Action, InMemoryMemoryStore, MemoryEntry, MemoryFilter
from app.persona import Fact, FakeEmbedder, InMemoryPersonaStore, PersonaQuery
from app.reflection import (
    StubReflector,
    reconcile_episode,
    run_reflection_pass,
)

FOOD = ["opinions", "likes", "food"]


@pytest.fixture(autouse=True)
def fresh_stores():
    memory.set_store(InMemoryMemoryStore())
    persona.set_store(InMemoryPersonaStore(FakeEmbedder()))


def log_episode(text, claim=None, tool="notes.remember"):
    """Append an episode; `claim` rides in event['durable_claim'] (pre-grounded)."""
    event = {"type": "user_request", "text": text}
    if claim is not None:
        event["durable_claim"] = claim
    return memory.append(
        MemoryEntry(event=event, action=Action(tool=tool))
    )


def all_food_facts():
    return persona.search(PersonaQuery(text="anything", category=FOOD, min_similarity=0.0, limit=50))


def test_contradicting_episode_updates_belief_in_place():
    # Persona already believes the opposite.
    fact_id = persona.upsert(Fact(text="dislikes bagels", category=FOOD))
    log_episode(
        "these bagels are actually great, I was wrong",
        claim={"text": "likes bagels", "category": FOOD},
    )

    result = reconcile_episode(
        memory.query(MemoryFilter(limit=1))[0], StubReflector(), min_similarity=0.0
    )

    assert result.action == "updated"
    assert result.fact_id == fact_id            # same belief, corrected in place
    facts = all_food_facts()
    assert len(facts) == 1                       # not duplicated
    assert facts[0].fact.text == "likes bagels"


def test_novel_claim_creates_new_belief():
    log_episode(
        "I've started cycling to campus",
        claim={"text": "commutes by bicycle", "category": ["routines", "transport"]},
    )
    result = reconcile_episode(
        memory.query(MemoryFilter(limit=1))[0], StubReflector(), min_similarity=0.0
    )

    assert result.action == "created"
    assert persona.get(result.fact_id).text == "commutes by bicycle"


def test_episode_without_a_claim_is_noop():
    persona.upsert(Fact(text="dislikes bagels", category=FOOD))
    log_episode("what time is my lecture")  # no durable_claim

    result = reconcile_episode(
        memory.query(MemoryFilter(limit=1))[0], StubReflector(), min_similarity=0.0
    )

    assert result.action == "noop"
    assert len(all_food_facts()) == 1
    assert all_food_facts()[0].fact.text == "dislikes bagels"  # untouched


def test_reconciled_episode_is_marked_reflected():
    log_episode("x", claim={"text": "likes tea", "category": FOOD})
    entry = memory.query(MemoryFilter(limit=1))[0]
    reconcile_episode(entry, StubReflector(), min_similarity=0.0)

    reprocessed = memory.query(MemoryFilter(limit=1))[0]
    assert reprocessed.outcome is not None
    assert reprocessed.outcome.detail.get("reflected") is True


def test_pass_is_idempotent():
    persona.upsert(Fact(text="dislikes bagels", category=FOOD))
    log_episode("great bagels", claim={"text": "likes bagels", "category": FOOD})

    first = run_reflection_pass(min_similarity=0.0)
    second = run_reflection_pass(min_similarity=0.0)

    assert len(first) == 1 and first[0].action == "updated"
    assert second == []                          # nothing left to reflect
    assert len(all_food_facts()) == 1            # not applied twice


def test_pass_processes_only_unreflected_and_preserves_prior_outcome():
    # An episode that already has a real outcome still gets reflected, keeping status.
    entry_id = log_episode("great bagels", claim={"text": "likes bagels", "category": FOOD})
    memory.update_outcome(entry_id, memory.Outcome(status="accepted"))

    run_reflection_pass(min_similarity=0.0)

    entry = memory.query(MemoryFilter(limit=1))[0]
    assert entry.outcome.status == "accepted"          # prior status preserved
    assert entry.outcome.detail.get("reflected") is True
