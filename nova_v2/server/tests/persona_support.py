"""Test doubles for Persona reconciliation.

FakeEmbedder gives every text a near-orthogonal vector, so nothing is ever
"close" and remember() never reaches the judge. GroupEmbedder puts chosen
texts in the same neighbourhood (cosine ~0.9, like a real reword or negation
under bge), and StubJudge answers from a table instead of a model.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

from app.store.persona import FakeEmbedder, JudgeUnavailable, normalise_text


class GroupEmbedder:
    """Texts named in `groups` share their group's direction; everything else
    falls back to FakeEmbedder (unrelated to all)."""

    def __init__(self, groups: Optional[dict[str, str]] = None) -> None:
        self._fake = FakeEmbedder()
        self.dim = self._fake.dim
        self.groups: dict[str, str] = {}
        for text, group in (groups or {}).items():
            self.add(group, text)

    def add(self, group: str, *texts: str) -> None:
        for t in texts:
            self.groups[normalise_text(t)] = group

    def embed(self, texts: list[str], input_type: str = "document") -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        group = self.groups.get(normalise_text(text))
        if group is None:
            return self._fake._one(text)
        base = self._fake._one(f"group:{group}")
        noise = self._fake._one(f"noise:{text}")
        vec = [0.95 * b + 0.3 * n for b, n in zip(base, noise)]
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


class StubJudge:
    """Relations from a table keyed by the unordered pair of texts; anything
    not in the table is unrelated. Records every call."""

    def __init__(self, default: str = "unrelated") -> None:
        self.table: dict[frozenset[str], str] = {}
        self.default = default
        self.calls: list[tuple[str, list[str]]] = []
        self.fail = False

    def say(self, relation: str, *texts: str) -> "StubJudge":
        """Every pair among `texts` has `relation`."""
        norm = [normalise_text(t) for t in texts]
        for i, a in enumerate(norm):
            for b in norm[i + 1:]:
                self.table[frozenset((a, b))] = relation
        return self

    def duplicates(self, *texts: str) -> "StubJudge":
        return self.say("duplicate", *texts)

    def contradicts(self, a: str, b: str) -> "StubJudge":
        return self.say("contradicts", a, b)

    def __call__(self, new_text: str, existing: list[str]) -> list[str]:
        self.calls.append((new_text, list(existing)))
        if self.fail:
            raise JudgeUnavailable("stub judge told to fail")
        a = normalise_text(new_text)
        return [self.table.get(frozenset((a, normalise_text(t))), self.default) for t in existing]


def texts(facts: Iterable) -> list[str]:
    return sorted(f.text for f in facts)
