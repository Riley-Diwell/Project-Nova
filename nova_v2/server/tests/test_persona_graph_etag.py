"""GET /persona/graph's ETag: the phone keeps the last graph and asks "has it
changed?" with If-None-Match. It must answer 304 exactly when the graph it would
build is the one the phone already has - a stale "not modified" hides beliefs
from the user; a needless 200 only costs bandwidth."""
from __future__ import annotations

from app.store import persona
from test_tenancy import A, B, a_fact, as_, world  # noqa: F401 - world is a fixture


def _graph(client, user=A, etag=None, **params):
    headers = as_(user)
    if etag:
        headers["If-None-Match"] = etag
    return client.get("/persona/graph", params=params, headers=headers)


def test_unchanged_graph_is_304_without_a_body(world):
    a_fact(A)
    first = _graph(world["client"])
    assert first.status_code == 200
    etag = first.headers["ETag"]
    assert first.headers["Cache-Control"] == "private, no-cache"

    again = _graph(world["client"], etag=etag)
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["ETag"] == etag


def test_304_never_builds_the_graph(world, monkeypatch):
    a_fact(A)
    etag = _graph(world["client"]).headers["ETag"]

    def boom(*args, **kwargs):
        raise AssertionError("built the graph to answer a 304")

    monkeypatch.setattr(persona, "knowledge_graph", boom)
    assert _graph(world["client"], etag=etag).status_code == 304


def test_adding_editing_or_forgetting_a_fact_changes_the_tag(world):
    client = world["client"]
    fact_id = a_fact(A)
    tags = [_graph(client).headers["ETag"]]

    a_fact(A, text="likes coffee")
    tags.append(_graph(client).headers["ETag"])

    assert client.patch(f"/persona/{fact_id}", json={"text": "likes rye bagels"}, headers=as_(A)).status_code == 200
    tags.append(_graph(client).headers["ETag"])

    assert client.delete(f"/persona/{fact_id}", headers=as_(A)).status_code in (200, 204)
    tags.append(_graph(client).headers["ETag"])

    assert len(set(tags)) == len(tags)
    # And each old tag now gets the full graph, not a 304.
    for old in tags[:-1]:
        assert _graph(client, etag=old).status_code == 200


def test_the_tag_depends_on_link_density(world):
    a_fact(A)
    etag = _graph(world["client"], min_similarity=0.9).headers["ETag"]
    assert _graph(world["client"], etag=etag, min_similarity=0.9).status_code == 304
    assert _graph(world["client"], etag=etag, min_similarity=0.5).status_code == 200


def test_one_users_tag_is_not_anothers(world):
    a_fact(A)
    etag = _graph(world["client"], user=A).headers["ETag"]
    r = _graph(world["client"], user=B, etag=etag)
    assert r.status_code == 200
    assert r.json()["stats"]["facts"] == 0


def test_if_none_match_lists_and_weak_tags(world):
    a_fact(A)
    etag = _graph(world["client"]).headers["ETag"]
    assert _graph(world["client"], etag=f'"stale", W/{etag}').status_code == 304
    assert _graph(world["client"], etag='"stale"').status_code == 200
