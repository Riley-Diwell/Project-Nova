"""The Knowledge Map's History view: when a belief was first learned, and every
rewording the user made from the map, dated, oldest first."""
from __future__ import annotations

from test_tenancy import A, a_fact, as_, world  # noqa: F401 - world is a fixture


def _node(client, fact_id):
    graph = client.get("/persona/graph", headers=as_(A)).json()
    return next(n for n in graph["nodes"] if n["id"] == fact_id)


def test_a_new_fact_has_a_creation_date_and_no_edits(world):
    fact_id = a_fact(A, quote="I like bagels")
    node = _node(world["client"], fact_id)
    assert node["created_at"]
    assert node["edits"] == []
    assert node["detail"] == "I like bagels"


def test_each_rewording_is_logged_in_order(world):
    client = world["client"]
    fact_id = a_fact(A, quote="I like bagels")

    assert client.patch(f"/persona/{fact_id}", json={"text": "likes rye bagels"}, headers=as_(A)).status_code == 200
    assert client.patch(f"/persona/{fact_id}", json={"text": "likes sesame bagels"}, headers=as_(A)).status_code == 200

    node = _node(client, fact_id)
    assert [(e["from"], e["to"]) for e in node["edits"]] == [
        ("likes bagels", "likes rye bagels"),
        ("likes rye bagels", "likes sesame bagels"),
    ]
    assert all(e["at"] for e in node["edits"])
    assert node["edits"][0]["at"] <= node["edits"][1]["at"]
    # What the user originally said survives the edits.
    assert node["detail"] == "I like bagels"


def test_a_category_only_edit_logs_no_rewording(world):
    client = world["client"]
    fact_id = a_fact(A)
    assert client.patch(f"/persona/{fact_id}", json={"category": ["opinions", "food"]}, headers=as_(A)).status_code == 200
    assert _node(client, fact_id)["edits"] == []
