"""The Knowledge Map's meaning groups: facts join the nearest group or start
one, stay put when reworded, and groups are named once - plus the graph and
search endpoints that serve them."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from notes_support import USER
from persona_support import BlendEmbedder, StubTitler, texts

from app.store import persona
from app.store.persona import Fact, InMemoryClusterStore, InMemoryPersonaStore
from app.store.persona import clusters as groups_mod
from app.store.persona.clusters import refresh, title_pending
from test_tenancy import A, B, a_fact, as_, world  # noqa: F401 - world is a fixture

FOOD = ["opinions", "likes", "food"]


@pytest.fixture
def grouping():
    embedder = BlendEmbedder()
    store = InMemoryPersonaStore(embedder)
    groups = InMemoryClusterStore()
    persona.set_store(store)
    persona.set_embedder(embedder)
    persona.set_cluster_store(groups)
    yield store, groups, embedder
    persona.set_store(None)
    persona.set_embedder(None)
    persona.set_cluster_store(None)


def say(text, category=FOOD):
    return persona.remember(USER, Fact(text=text, category=category, metadata={"source": "stated"}))


def group_of(groups, fact_id):
    return groups.membership(USER).get(fact_id)


def by_group(groups):
    out: dict[str, list[str]] = {}
    facts = {f.id: f.text for f in persona.all_facts(USER)}
    for fact_id, cluster_id in groups.membership(USER).items():
        out.setdefault(cluster_id, []).append(facts[fact_id])
    return sorted(sorted(v) for v in out.values())


# --- placement --------------------------------------------------------------------

def test_a_close_fact_joins_the_group_and_a_far_one_starts_its_own(grouping):
    _, groups, emb = grouping
    emb.set("Likes apples", fruit=1)
    emb.set("Likes pears", fruit=1, pear=0.5)        # cos ~0.89 to the group
    emb.set("Takes the bus to uni", travel=1)
    a = say("Likes apples").fact_id
    b = say("Likes pears").fact_id
    c = say("Takes the bus to uni", ["routines"]).fact_id
    assert group_of(groups, a) == group_of(groups, b) != group_of(groups, c)
    assert by_group(groups) == [["Likes apples", "Likes pears"], ["Takes the bus to uni"]]


def test_a_reworded_fact_keeps_its_group_until_it_really_moves(grouping):
    store, groups, emb = grouping
    emb.set("Likes apples", fruit=1)
    fact = persona.get(USER, say("Likes apples").fact_id)
    home = group_of(groups, fact.id)

    emb.set("Likes apples, mostly green ones", fruit=1, green=1.265)   # cos ~0.62 >= STAY
    persona.remember(USER, fact.model_copy(update={"text": "Likes apples, mostly green ones"}))
    assert group_of(groups, fact.id) == home

    emb.set("Collects vinyl records", music=1)                          # nowhere near
    persona.remember(USER, persona.get(USER, fact.id).model_copy(update={"text": "Collects vinyl records"}))
    assert group_of(groups, fact.id) != home
    assert home not in {c.id for c in groups.clusters(USER)}           # emptied, dropped


def test_a_merged_duplicate_leaves_one_member(grouping):
    _, groups, emb = grouping
    emb.set("Likes apples", fruit=1)
    first = say("Likes apples")
    again = say("likes apples.")
    assert again.fact_id == first.fact_id
    assert list(groups.membership(USER)) == [first.fact_id]


def test_first_grouping_is_made_over_everything_at_once(grouping):
    store, groups, emb = grouping
    for text, mix in [("Likes apples", {"fruit": 1}), ("Takes the bus", {"travel": 1}),
                      ("Likes pears", {"fruit": 1, "pear": 0.4}), ("Cycles to uni", {"travel": 1, "bike": 0.4})]:
        emb.set(text, **mix)
        store.upsert(USER, Fact(text=text, category=FOOD))    # written before grouping existed
    assert groups.clusters(USER) == []
    result = refresh(groups, store, USER)
    assert result.created == 2
    assert by_group(groups) == [["Cycles to uni", "Takes the bus"], ["Likes apples", "Likes pears"]]


def test_refresh_groups_stragglers_drops_the_gone_and_moves_nobody(grouping):
    store, groups, emb = grouping
    emb.set("Likes apples", fruit=1)
    emb.set("Likes pears", fruit=1, pear=0.5)
    apples = say("Likes apples").fact_id
    home = group_of(groups, apples)
    pears = store.upsert(USER, Fact(text="Likes pears", category=FOOD))   # bypassed grouping
    refresh(groups, store, USER)
    assert group_of(groups, pears) == home == group_of(groups, apples)

    persona.delete(USER, apples)
    persona.delete(USER, pears)
    refresh(groups, store, USER)
    assert groups.clusters(USER) == [] and groups.membership(USER) == {}


# --- headings ---------------------------------------------------------------------------

def test_groups_are_named_once_and_again_only_after_growing_a_lot(grouping):
    store, groups, emb = grouping
    emb.set("Likes apples", fruit=1)
    say("Likes apples")
    [group] = groups.clusters(USER)
    assert group.title == "Likes apples" and group.title_source == groups_mod.TITLE_PENDING

    titler = StubTitler("Fruit")
    assert title_pending(groups, store, USER, titler) == 1
    assert title_pending(groups, store, USER, titler) == 0          # named: left alone
    assert groups.clusters(USER)[0].title == "Fruit"
    assert len(titler.calls) == 1

    for i in range(5):
        emb.set(f"Likes fruit {i}", fruit=1, **{f"f{i}": 0.3})
        say(f"Likes fruit {i}")
    assert groups.clusters(USER)[0].size == 6
    assert title_pending(groups, store, USER, titler) == 1          # grew 1 -> 6: renamed
    assert len(titler.calls) == 2


def test_a_failed_titler_leaves_a_stand_in_and_is_retried(grouping):
    store, groups, emb = grouping
    emb.set("Is doing a mechanical engineering degree at ANU", study=1)
    say("Is doing a mechanical engineering degree at ANU", ["facts", "courses"])
    titler = StubTitler()
    titler.fail = True
    assert title_pending(groups, store, USER, titler) == 0
    [group] = groups.clusters(USER)
    assert group.title_source == groups_mod.TITLE_FALLBACK
    assert group.title.endswith("…") and len(group.title) <= 33

    titler.fail = False
    assert title_pending(groups, store, USER, titler) == 1


def test_a_heading_the_user_chose_is_never_overwritten(grouping):
    store, groups, emb = grouping
    emb.set("Likes apples", fruit=1)
    say("Likes apples")
    [group] = groups.clusters(USER)
    groups.save(USER, groups_mod.replace(group, title="Snacks", title_source=groups_mod.TITLE_USER))
    assert title_pending(groups, store, USER, StubTitler()) == 0
    assert groups.clusters(USER)[0].title == "Snacks"


# --- the graph and search endpoints ------------------------------------------------------

def _graph(client, etag=None, **params):
    headers = as_(A)
    if etag:
        headers["If-None-Match"] = etag
    return client.get("/persona/graph", params=params, headers=headers)


@pytest.fixture
def grouped_world(world):
    groups = InMemoryClusterStore()
    persona.set_cluster_store(groups)
    yield {**world, "groups": groups}
    persona.set_cluster_store(None)


def grouped(world_):
    """What a consolidation pass does for facts written around remember()."""
    refresh(world_["groups"], persona.get_store(), A)


def test_graph_with_clusters_has_groups_and_no_category_skeleton(grouped_world):
    a_fact(A)
    a_fact(A, text="likes fish fingers")
    grouped(grouped_world)
    body = _graph(grouped_world["client"], clusters="true").json()
    kinds = {n["kind"] for n in body["nodes"]}
    assert "cluster" in kinds and "category" not in kinds
    facts = [n for n in body["nodes"] if n["kind"] == "fact"]
    clusters = {n["id"]: n for n in body["nodes"] if n["kind"] == "cluster"}
    assert all(f["cluster"] in clusters for f in facts)
    assert sum(c["size"] for c in clusters.values()) == 2
    assert all(f["stated_at"] for f in facts)

    # Without asking, the old shape: the category skeleton, no groups.
    plain = _graph(grouped_world["client"]).json()
    assert {n["kind"] for n in plain["nodes"]} == {"fact", "category"}


def test_renaming_a_group_changes_the_tag_without_touching_a_fact(grouped_world):
    a_fact(A)
    grouped(grouped_world)
    first = _graph(grouped_world["client"], clusters="true")
    assert _graph(grouped_world["client"], first.headers["ETag"], clusters="true").status_code == 304

    groups = grouped_world["groups"]
    [group] = groups.clusters(A)
    groups.save(A, groups_mod.replace(group, title="Food"))
    renamed = _graph(grouped_world["client"], first.headers["ETag"], clusters="true")
    assert renamed.status_code == 200
    assert [n["label"] for n in renamed.json()["nodes"] if n["kind"] == "cluster"] == ["Food"]


def test_search_finds_by_word_and_names_the_group_to_fly_to(grouped_world):
    fact_id = a_fact(A, text="Is taking COMP2100 this semester")
    a_fact(A, text="likes fish fingers")
    a_fact(B, text="Is taking COMP2100 too")
    grouped(grouped_world)
    r = grouped_world["client"].get("/persona/search", params={"q": "COMP2100"}, headers=as_(A))
    assert r.status_code == 200
    body = r.json()
    [hit] = body["hits"]                        # only A's, and only the match
    assert hit["fact_id"] == fact_id and hit["match"] == "keyword"
    assert body["target_cluster"] == hit["cluster_id"] is not None


def test_search_needs_a_query(world):
    assert world["client"].get("/persona/search", params={"q": ""}, headers=as_(A)).status_code == 422
