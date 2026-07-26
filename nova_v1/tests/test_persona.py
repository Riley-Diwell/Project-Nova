"""Tests for the Persona store seam (section 5.4) against the in-memory fake.

These run with no Supabase and no embedding model: `FakeEmbedder` gives deterministic
vectors, and `InMemoryPersonaStore` shares the PersonaStore contract with the
Supabase implementation, so passing here means the seam behaves as promised.

Because the fake embedder is not semantically meaningful, these assert on
exact/self recall and mechanics (store, update-in-place, category filter,
delete), not fuzzy semantic ranking.
"""
import pytest

from app.persona import (
    Fact,
    FactNotFound,
    FakeEmbedder,
    InMemoryPersonaStore,
    PersonaQuery,
    delete,
    get,
    search,
    set_store,
    upsert,
)


@pytest.fixture(autouse=True)
def fresh_store():
    set_store(InMemoryPersonaStore(FakeEmbedder()))


def test_upsert_then_recall_by_semantic_search():
    fact_id = upsert(Fact(text="likes bagels", category=["opinions", "likes", "food"]))
    assert fact_id

    # querying the exact text returns that fact as the top (self-similar) match
    hits = search(PersonaQuery(text="likes bagels", limit=5))
    assert hits
    assert hits[0].fact.id == fact_id
    assert hits[0].similarity == pytest.approx(1.0, abs=1e-6)


def test_correction_updates_belief_in_place():
    # Section 5.5 done-when (b): a contradicting episode corrects Persona.
    fact_id = upsert(Fact(text="likes bagels", category=["opinions", "likes", "food"]))
    upsert(Fact(id=fact_id, text="dislikes bagels", category=["opinions", "likes", "food"]))

    updated = get(fact_id)
    assert updated.text == "dislikes bagels"
    assert updated.created_at <= updated.updated_at  # timestamps preserved/advanced
    # still a single belief, not a duplicate
    assert len(search(PersonaQuery(text="dislikes bagels", limit=10))) == 1


def test_category_filter_restricts_to_subtree():
    upsert(Fact(text="likes bagels", category=["opinions", "likes", "food"]))
    upsert(Fact(text="studies mechanical engineering", category=["facts", "courses"]))

    food = search(PersonaQuery(text="anything", category=["opinions", "likes"], limit=10))
    assert all(m.fact.category[:2] == ["opinions", "likes"] for m in food)
    assert len(food) == 1


def test_min_similarity_drops_weak_matches():
    upsert(Fact(text="likes bagels"))
    # a query the fake maps far away, with a high floor, yields nothing
    assert search(PersonaQuery(text="completely unrelated", min_similarity=0.99)) == []


def test_limit_caps_results():
    for i in range(5):
        upsert(Fact(text=f"belief number {i}"))
    assert len(search(PersonaQuery(text="belief", limit=3))) == 3


def test_delete_removes_belief():
    fact_id = upsert(Fact(text="likes bagels"))
    delete(fact_id)
    with pytest.raises(FactNotFound):
        get(fact_id)


def test_get_missing_raises():
    with pytest.raises(FactNotFound):
        get("does-not-exist")
