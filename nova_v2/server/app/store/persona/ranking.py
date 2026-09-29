"""Hybrid ranking for Persona search - era-memory's, over our own store.

WHY HYBRID
An embedding alone is weak on exactly the things users name: course codes,
places, people. bge-large sees "COMP2100" as noise, so "what did I say about
COMP2100" can rank a fact that literally contains it below one that merely
sounds academic. era-memory (github.com/Era-Laboratories/era-memory) fixes
that by running a vector search and a keyword search side by side and fusing
the two ranked lists with Reciprocal Rank Fusion. Rank rather than score, so
cosine and ts_rank - which live on unrelated scales - fuse cleanly.

WHAT IS TAKEN FROM era-memory, AND WHAT IS NOT
Its pure ranking functions (`rrf_fuse`, `recency_decay`, `final_score`), with
its defaults: k=60, weights 0.6 semantic / 0.4 lexical, recency weight 0.3.
Not its stores: those are insert-only and have no notion of one user's belief
superseding another, which is the whole point of reconcile.py. This module is
the only place era_memory is imported - `core.logic` is not its documented
public API, so the dependency is pinned and kept to one seam.

One deliberate change: the half-life. era-memory decays at 30 days because it
stores episodes. Persona stores durable facts - "allergic to peanuts" said
once in March is not a quarter as true by May - so recency here is a gentle
tie-breaker with a much longer half-life, not a fade.

Both stores call rank(), so tests exercise the same fusion the server runs.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from era_memory.core.logic.rrf import final_score, recency_decay, rrf_fuse

from app.store.persona.models import Fact, Match

RRF_K = 60
SEMANTIC_WEIGHT = 0.6
LEXICAL_WEIGHT = 0.4
RECENCY_WEIGHT = 0.3
PERSONA_HALF_LIFE_DAYS = 120.0


def rank(
    matches: list[Match],
    *,
    min_similarity: float = 0.0,
    limit: Optional[int] = None,
    now: Optional[datetime] = None,
) -> list[Match]:
    """Fuse raw candidates (each carrying `similarity` and `lexical`) into one
    list ordered by `score`, best first.

    `min_similarity` gates only the semantic list: a fact that matches the
    query's words exactly still comes back when its embedding is far off,
    which is what the keyword half is for.
    """
    now = now or datetime.now(timezone.utc)
    semantic = sorted(
        (m for m in matches if m.similarity >= min_similarity and m.fact.id),
        key=lambda m: m.similarity, reverse=True,
    )
    lexical = sorted(
        (m for m in matches if m.lexical > 0 and m.fact.id),
        key=lambda m: m.lexical, reverse=True,
    )
    fused = rrf_fuse(
        [(m.fact.id, m.similarity) for m in semantic],
        [(m.fact.id, m.lexical) for m in lexical],
        k=RRF_K, semantic_weight=SEMANTIC_WEIGHT, lexical_weight=LEXICAL_WEIGHT,
    )

    ranked: list[Match] = []
    seen: set[str] = set()
    for m in matches:
        fid = m.fact.id
        if fid not in fused or fid in seen:
            continue
        seen.add(fid)
        recency = recency_decay(_age_days(m.fact, now), half_life_days=PERSONA_HALF_LIFE_DAYS)
        score = final_score(fused[fid], m.fact.confidence, recency, recency_weight=RECENCY_WEIGHT)
        ranked.append(m.model_copy(update={"score": score}))

    ranked.sort(key=lambda m: (m.score, m.similarity), reverse=True)
    return ranked[:limit] if limit is not None else ranked


def _age_days(fact: Fact, now: datetime) -> float:
    when = fact.stated_at or fact.updated_at or fact.created_at
    if when is None:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (now - when).total_seconds() / 86400.0)


# --- in-memory stand-in for Postgres full-text -------------------------------

# Roughly Postgres' english stopword list, trimmed to what facts actually say.
# Like Postgres it drops "not"/"no": the keyword half is recall, never the
# judge of meaning, so losing negation here costs nothing.
_STOPWORDS = frozenset("""
a about above after again against all am an and any are as at be because been
before being below between both but by can could did do does doing don doesn
down during each few for from further had has have having he her here hers him
his how i if in into is it its itself just me more most my myself no nor not
now of off on once only or other our ours out over own same she should so some
such t than that the their theirs them then there these they this those through
to too under until up user users very was we were what when where which while
who whom why will with would you your yours s re ve ll d m
""".split())

_WORD = re.compile(r"[a-z0-9]+")


def terms(text: str) -> set[str]:
    """Lower-cased, stopword-free, crudely stemmed tokens - enough for the
    in-memory store to behave like to_tsvector('english', ...) in tests."""
    out: set[str] = set()
    for word in _WORD.findall(str(text).lower()):
        if word in _STOPWORDS:
            continue
        if len(word) > 4 and word.endswith("ing"):
            word = word[:-3]
        elif len(word) > 3 and word.endswith("es") and not word.endswith("ses"):
            word = word[:-1]
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.add(word)
    return out


def lexical_score(query: str, text: str, *, any_terms: bool = False) -> float:
    """A ts_rank stand-in in [0, 1]. All query terms must appear unless
    `any_terms` (write-time candidate recall, which ORs them)."""
    q, t = terms(query), terms(text)
    if not q or not t:
        return 0.0
    hit = q & t
    if not hit or (not any_terms and hit != q):
        return 0.0
    return len(hit) / len(q | t)
