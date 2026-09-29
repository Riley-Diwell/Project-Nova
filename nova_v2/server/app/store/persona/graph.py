"""The Knowledge Map's data (Section 5.6): Persona as a navigable graph.

Two kinds of link, because the store has two kinds of structure and they say
different things:

  - CATEGORY links are the ontology, and they are exact. A fact filed at
    ["opinions","likes","food"] hangs off a chain opinions -> likes -> food.
    This is the skeleton: it never lies, and it groups things the user (or the
    model) explicitly decided belong together.
  - SIMILAR links are the embeddings, and they are discovered. Two facts are
    linked when their vectors are close, whatever categories they were filed
    under. This is what surfaces the connections nobody declared - the reason
    the map is worth looking at rather than just a folder tree.

WHY A THRESHOLD WORKS HERE AND NOT IN RETRIEVAL
intent_surface.py's PERSONA_MIN_SIMILARITY has to be near-useless (0.35) because it
compares a *question* to a *statement*, and those two bands overlap. Here both
sides are stored facts - same voice, same shape, no query instruction - and the
distribution separates cleanly. Measured on real facts:

    0.818  "Regularly parks on level 4 at Kambri" | "Parks in the Kambri car park"
    0.716  "Likes fish fingers"                   | "Likes dinosaur nuggets"
    0.702  "Likes fish fingers"                   | "Likes baked beans"
    ---- gap ----
    0.548  "...visit Brooklyn Boy Bagels..."      | "...Slack notifications..."
    0.366  "...visit Brooklyn Boy Bagels..."      | "Parks in the Kambri car park"

DEFAULT_MIN_SIMILARITY sits in that gap. It is exposed as a query parameter so
the app can loosen it - a sparser or denser map is a taste question, and the
right value will drift as the store grows.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.store.persona.models import Fact

# In the gap between "related" and "unrelated" measured above. Tunable per
# request; see the module docstring before changing the default.
DEFAULT_MIN_SIMILARITY = 0.65

# Keep only this many similarity links per fact, strongest first. Without a cap
# a dense cluster (three food preferences all near each other) turns into a
# hairball that hides the structure it was meant to show.
DEFAULT_MAX_LINKS = 4

NodeKind = Literal["fact", "category", "cluster"]
EdgeKind = Literal["category", "similar"]


class GraphNode(BaseModel):
    id: str
    label: str
    kind: NodeKind

    # Facts only. `source` is "stated" or "derived" (see consolidation) and is
    # what the map colours by - the user can see at a glance what NOVA was told
    # versus what it worked out.
    source: Optional[str] = None
    confidence: Optional[float] = None
    category: list[str] = Field(default_factory=list)
    support: Optional[int] = None
    detail: Optional[str] = None
    # The note a belief was promoted from, if any - the Knowledge Map's
    # "From your note" link.
    note_id: Optional[str] = None
    # When the belief was last said or seen (Fact.stated_at), ISO 8601.
    stated_at: Optional[str] = None
    # A fact's meaning group ("cluster:<id>") - the map's subheading for it.
    cluster: Optional[str] = None
    # A cluster node's member count.
    size: Optional[int] = None


class GraphEdge(BaseModel):
    source: str
    target: str
    kind: EdgeKind
    weight: float = 1.0


class KnowledgeGraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    def stats(self) -> dict[str, int]:
        return {
            "facts": sum(1 for n in self.nodes if n.kind == "fact"),
            "categories": sum(1 for n in self.nodes if n.kind == "category"),
            "clusters": sum(1 for n in self.nodes if n.kind == "cluster"),
            "category_links": sum(1 for e in self.edges if e.kind == "category"),
            "similar_links": sum(1 for e in self.edges if e.kind == "similar"),
        }


def build_graph(
    facts: list[Fact],
    vectors: dict[str, list[float]],
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    max_links: int = DEFAULT_MAX_LINKS,
    clusters: Optional[Any] = None,
) -> KnowledgeGraph:
    """Nodes and edges for the Knowledge Map.

    With `clusters` (a clusters.ClusterView), facts are grouped by meaning:
    one "cluster:<id>" node per group, headed and sized, each fact naming its
    group - and no category skeleton, which the groups replace on the map.
    Facts still carry their category path for their detail card.
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    category_ids: set[str] = set()
    membership = clusters.membership if clusters is not None else {}

    for fact in facts:
        if not fact.id:
            continue
        meta = fact.metadata or {}
        nodes.append(GraphNode(
            id=fact.id,
            label=fact.text,
            kind="fact",
            source=meta.get("source", "stated"),
            confidence=fact.confidence,
            category=list(fact.category),
            support=meta.get("support"),
            detail=meta.get("quote") or meta.get("value"),
            note_id=str(meta["note_id"]) if meta.get("note_id") else None,
            stated_at=fact.stated_at.isoformat() if fact.stated_at else None,
            cluster=f"cluster:{membership[fact.id]}" if fact.id in membership else None,
        ))
        if clusters is None:
            edges.extend(_category_chain(fact, category_ids, nodes))

    if clusters is not None:
        present = {f.id for f in facts if f.id}
        sizes: dict[str, int] = {}
        for fact_id, cluster_id in membership.items():
            if fact_id in present:
                sizes[cluster_id] = sizes.get(cluster_id, 0) + 1
        nodes.extend(
            GraphNode(id=f"cluster:{c.id}", label=c.title, kind="cluster", size=sizes[c.id])
            for c in sorted(clusters.clusters, key=lambda c: c.created_at)
            if sizes.get(c.id)
        )

    edges.extend(_similarity_edges(facts, vectors, min_similarity, max_links))
    return KnowledgeGraph(nodes=nodes, edges=edges)


def _category_chain(
    fact: Fact, seen: set[str], nodes: list[GraphNode]
) -> list[GraphEdge]:
    """The ontology path as a chain of nodes, with the fact hanging off the end.

    ["opinions","likes","food"] gives nodes opinions, opinions/likes,
    opinions/likes/food - so two facts filed under different leaves of the same
    branch still meet further up, which is what makes the map navigable rather
    than a flat scatter.
    """
    edges: list[GraphEdge] = []
    parent: Optional[str] = None

    for depth in range(1, len(fact.category) + 1):
        path = fact.category[:depth]
        node_id = "cat:" + "/".join(path)
        if node_id not in seen:
            seen.add(node_id)
            nodes.append(GraphNode(
                id=node_id, label=path[-1], kind="category", category=list(path),
            ))
        if parent is not None:
            edges.append(GraphEdge(source=parent, target=node_id, kind="category"))
        parent = node_id

    if parent is not None and fact.id:
        edges.append(GraphEdge(source=parent, target=fact.id, kind="category"))
    return edges


def _similarity_edges(
    facts: list[Fact],
    vectors: dict[str, list[float]],
    min_similarity: float,
    max_links: int,
) -> list[GraphEdge]:
    """Undirected links between facts whose vectors are close.

    Each fact proposes its `max_links` strongest neighbours and the union is
    kept, so a fact is never left isolated just because its neighbours each
    had better options - the cap thins hairballs without severing anything.
    """
    ids = [f.id for f in facts if f.id and f.id in vectors]
    if len(ids) < 2:
        return []

    scored: dict[str, list[tuple[float, str]]] = {i: [] for i in ids}
    for a_pos, a in enumerate(ids):
        for b in ids[a_pos + 1:]:
            score = _cosine(vectors[a], vectors[b])
            if score >= min_similarity:
                scored[a].append((score, b))
                scored[b].append((score, a))

    kept: dict[tuple[str, str], float] = {}
    for node_id, neighbours in scored.items():
        neighbours.sort(reverse=True)
        for score, other in neighbours[:max_links]:
            key = (node_id, other) if node_id < other else (other, node_id)
            kept[key] = score

    return [
        GraphEdge(source=a, target=b, kind="similar", weight=round(score, 3))
        for (a, b), score in sorted(kept.items(), key=lambda kv: -kv[1])
    ]


def _cosine(a: list[float], b: list[float]) -> float:
    """Vectors are stored L2-normalised, so the dot product is the cosine."""
    return max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))
