"""The Knowledge Map's topics: every fact sits under one of a fixed list of
topics, guessed at once and confirmed by the classifier, and a topic is never
renamed - plus the graph and search endpoints that serve them."""
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


def title_of(groups, fact_id):
    return next(c.title for c in groups.clusters(USER) if c.id == group_of(groups, fact_id))


def test_a_reworded_fact_keeps_its_topic_and_a_new_subject_moves_it(grouping):
    store, groups, emb = grouping
    emb.set("Likes apples", fruit=1)
    fact = persona.get(USER, say("Likes apples").fact_id)
    home = group_of(groups, fact.id)
    assert title_of(groups, fact.id) == "Food & drink"                 # from the category

    emb.set("Likes apples, mostly green ones", fruit=1, green=1.265)
    persona.remember(USER, fact.model_copy(update={"text": "Likes apples, mostly green ones"}))
    assert group_of(groups, fact.id) == home

    emb.set("Collects vinyl records", music=1)
    persona.remember(USER, persona.get(USER, fact.id).model_copy(
        update={"text": "Collects vinyl records", "category": ["interests", "music"]}))
    assert title_of(groups, fact.id) == "Entertainment"
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
    for text in ("Likes pizza", "Takes the bus", "Drinks coffee every day", "Drives the car on weekends"):
        store.upsert(USER, Fact(text=text, category=FOOD))    # written before topics existed
    assert groups.clusters(USER) == []
    result = refresh(groups, store, USER)
    assert result.created == 2 and result.placed == 4 and result.confirmed == 4
    assert by_group(groups) == [["Drinks coffee every day", "Likes pizza"],
                                ["Drives the car on weekends", "Takes the bus"]]
    assert groups.unconfirmed(USER) == set()


def test_refresh_places_stragglers_drops_the_gone_and_moves_nobody(grouping):
    store, groups, emb = grouping
    pizza = say("Likes pizza").fact_id
    home = group_of(groups, pizza)
    coffee = store.upsert(USER, Fact(text="Likes coffee", category=FOOD))   # bypassed placement
    refresh(groups, store, USER)
    assert group_of(groups, coffee) == home == group_of(groups, pizza)

    persona.delete(USER, pizza)
    persona.delete(USER, coffee)
    refresh(groups, store, USER)
    assert groups.clusters(USER) == [] and groups.membership(USER) == {}


# --- headings ---------------------------------------------------------------------------

def test_topics_are_named_by_the_list_and_never_renamed(grouping):
    store, groups, emb = grouping
    say("Likes pizza")
    [group] = groups.clusters(USER)
    assert group.title == "Food & drink" and group.title_source == groups_mod.TITLE_TOPIC

    titler = StubTitler("Snacks")
    for food in ("Likes sushi", "Loves coffee", "Eats breakfast late", "Likes tea", "Cooks on Sundays"):
        say(food)
    assert groups.clusters(USER)[0].size == 6
    assert title_pending(groups, store, USER, titler) == 0          # grew, still not renamed
    assert groups.clusters(USER)[0].title == "Food & drink"
    assert titler.calls == []


def test_a_failed_classifier_keeps_the_guess_and_the_next_pass_confirms_it(grouping):
    store, groups, emb = grouping
    fact_id = store.upsert(USER, Fact(text="Is doing a mechanical engineering degree at ANU",
                                      category=["facts", "courses"]))

    def down(texts):
        raise RuntimeError("model unreachable")

    refresh(groups, store, USER, classifier=down)
    assert title_of(groups, fact_id) == "Study"                        # guessed from the category
    assert groups.unconfirmed(USER) == {fact_id}

    assert refresh(groups, store, USER).confirmed == 1
    assert groups.unconfirmed(USER) == set()


def test_old_embedding_groups_become_topics_on_the_first_pass(grouping):
    store, groups, emb = grouping
    fact_id = store.upsert(USER, Fact(text="Likes pizza", category=FOOD))
    groups.create(USER, groups_mod.Cluster(id="old", title="Likes pizza", centroid=store.vector(USER, fact_id),
                                           size=1, title_source=groups_mod.TITLE_PENDING))
    groups.assign(USER, fact_id, "old")

    result = refresh(groups, store, USER)
    assert result.migrated
    [topic] = groups.clusters(USER)
    assert topic.is_topic and topic.title == "Food & drink"
    assert groups.membership(USER) == {fact_id: topic.id}


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
