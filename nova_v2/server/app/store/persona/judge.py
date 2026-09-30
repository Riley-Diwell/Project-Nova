"""The relation judge: is a new belief a duplicate of, a contradiction of, or
unrelated to each belief already held?

WHY A MODEL
Embeddings measure topic, not truth. bge-large puts "Likes apples" and
"Doesn't like apples" closer together than "Likes apples" and "Enjoys eating
apples" - negation barely moves the vector. So similarity can say "these are
about the same thing" and nothing more; whether they are the same claim, or
opposite claims, needs reading. The old memory tool used a cosine threshold
for this and could not tell a reword from a reversal from a neighbour.

WHAT THE JUDGE DOES NOT DO
Decide who wins. It sees only texts - no timestamps, no sources, no categories
- and labels relations. reconcile.py applies "most recent wins" in code, where
it is checkable and cannot be talked out of.

Injectable like every other model call here (consolidation's phraser and
extractor): tests pass a stub, and NOVA_MOCK_LLM gets NullJudge, which makes
reconcile fall back to exact matching only.
"""
from __future__ import annotations

import json
from typing import Any, Literal, Optional, Protocol

from pydantic import BaseModel

from app.core import llm

MODEL = llm.MODEL
MAX_OUTPUT_TOKENS = 512

Relation = Literal["duplicate", "contradicts", "unrelated"]
RELATIONS: tuple[str, ...] = ("duplicate", "contradicts", "unrelated")


class Verdict(BaseModel):
    index: int
    relation: Relation
    reason: str = ""


class Judgement(BaseModel):
    verdicts: list[Verdict]


class JudgeUnavailable(RuntimeError):
    """The judge could not give a usable answer (no key, mock mode, timeout,
    refusal, malformed output). reconcile writes the fact anyway, marked
    unreconciled, and the next sweep resolves it - a user's save is never lost
    to a failed model call."""


class Judge(Protocol):
    def __call__(self, new_text: str, existing: list[str]) -> list[str]: ...


JUDGE_PROMPT = """\
You compare ONE new statement about a user with numbered existing statements \
about the same user, for a personal assistant's long-term memory. For each \
existing statement, label how it relates to the new one. You are NOT deciding \
which is true or which is newer - only the relation.

Labels:
- "duplicate": the same claim, reworded. Neither says anything the other \
lacks. e.g. "Likes apples" / "Is fond of apples" / "Enjoys eating apples"; \
"Studies mechanical engineering" / "Is doing a mech eng degree".
- "contradicts": both cannot be true of the user at the same time. Negation, \
a reversed preference, a different value for something that has one value \
(lives in X vs lives in Y, usual way to get to uni), or a habit that has \
stopped. e.g. "Likes apples" / "Doesn't like apples"; "Drives to uni" / \
"Usually takes the bus to uni"; "Drinks coffee every morning" / "Has given up \
coffee".
- "unrelated": everything else - including the same topic when both can be \
true ("Likes apples" / "Likes bananas"), a compatible more specific claim \
("Likes coffee" / "Drinks a long black every morning"), or a merely related \
fact ("Is allergic to peanuts" / "Likes Thai food").

More examples:
- "Lives in Canberra" / "Lives in Sydney" -> contradicts
- "Hates early mornings" / "Is not a morning person" -> duplicate
- "Prefers texts to calls" / "Doesn't like phone calls" -> duplicate
- "Has a sister called Maya" / "Has a brother called Tom" -> unrelated
- "Goes to the gym on weekdays" / "Doesn't go to the gym any more" -> contradicts
- "Wants Nova to interrupt only for urgent things" / "Is happy for Nova to \
suggest things proactively" -> contradicts

Ignore wording style and grammatical person. When unsure between duplicate \
and unrelated, choose unrelated; when unsure between contradicts and \
unrelated, choose unrelated.

Input is JSON: {"new": "...", "existing": [{"index": 0, "text": "..."}, ...]}. \
Return exactly one verdict per existing statement, with its index, the \
relation, and a reason of at most one short sentence."""


class ModelJudge:
    """The model with structured output (core/llm.py parse()), as
    notes_pipeline/summarise.py does. `client` (OpenAI-compatible) is
    injectable for tests."""

    def __init__(self, client: Any = None, model: str = MODEL,
                 timeout_s: float = 8.0, max_retries: int = 1) -> None:
        self._client = client
        self._model = model
        self._timeout_s = timeout_s
        self._max_retries = max_retries

    def __call__(self, new_text: str, existing: list[str]) -> list[str]:
        if not existing:
            return []
        payload = {"new": new_text,
                   "existing": [{"index": i, "text": t} for i, t in enumerate(existing)]}
        try:
            judgement = llm.parse(
                JUDGE_PROMPT, json.dumps(payload), Judgement,
                max_tokens=MAX_OUTPUT_TOKENS, timeout=self._timeout_s,
                max_retries=self._max_retries, llm=self._client, model=self._model,
            )
        except Exception as e:
            raise JudgeUnavailable(f"judge call failed: {e}") from e
        return relations_from(judgement, len(existing))


# The name it had while the model was Claude; kept so imports don't break.
ClaudeJudge = ModelJudge


def relations_from(judgement: Judgement, count: int) -> list[str]:
    """One relation per existing statement, in order. A missing or
    out-of-range index is a malformed answer, not an implied "unrelated": a
    silent gap would let a contradiction through."""
    out: list[Optional[str]] = [None] * count
    for v in judgement.verdicts:
        if not 0 <= v.index < count:
            raise JudgeUnavailable(f"judge returned index {v.index} of {count}")
        out[v.index] = v.relation
    if any(r is None for r in out):
        raise JudgeUnavailable("judge skipped a statement")
    return [str(r) for r in out]


class NullJudge:
    """No model available (NOVA_MOCK_LLM). Every call is unavailable, so only
    exact duplicates are merged and everything else waits for a real sweep."""

    def __call__(self, new_text: str, existing: list[str]) -> list[str]:
        if not existing:
            return []
        raise JudgeUnavailable("no judge configured (NOVA_MOCK_LLM)")
