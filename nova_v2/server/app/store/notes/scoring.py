"""How a notes search ranks what it finds - shared by both store backends.

HYBRID, BECAUSE NEITHER HALF IS ENOUGH ON ITS OWN
  - Meaning (bge embeddings over the note and its chunks) finds "what did I
    note about the tutor" -> "ask tutor about Q3" whatever the wording.
  - Exact tokens (Postgres full-text search / a token match in memory) find
    "Q3" and "COMP2100", which bge embeds poorly: a course code is one rare
    token in a 1024-dim average and barely moves the vector.

The Supabase backend gets `similarity` and `lexical` from match_notes() and the
in-memory one computes them itself; everything after that - the weighting, the
recency decay, the snippet - lives here so tests exercise the same ranking
production uses.

RECENCY ONLY FOR QUICK NOTES
"Parked on level 3" goes stale in a day, so a quick note's score halves every
QUICK_HALF_LIFE_DAYS - never below half, because an old quick note that matches
exactly should still beat nothing. A lecture capture is as useful in week 12 as
in week 3, so dictations and captures do not decay.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

# Noise floor for the meaning half, same regime and same number as the memory
# tool's RECALL_MIN_SIMILARITY - see intent_surface.py's PERSONA_MIN_SIMILARITY
# for the measurements. Below this a hit only counts if a token matched.
MIN_SIMILARITY = 0.35

# How much a full token match is worth relative to a perfect embedding match.
LEXICAL_WEIGHT = 0.5

QUICK_HALF_LIFE_DAYS = 14.0

# Words that match everything and so mean nothing to the token half.
_STOPWORDS = frozenset(
    "a an and are about as at be did do for from have i in is it me my note "
    "noted notes of on or say said that the this to was what when where which "
    "who why with you".split()
)

SNIPPET_CHARS = 240


def tokens(text: str) -> list[str]:
    """Lowercased alphanumeric tokens, minus stopwords. Keeps 'q3' and '2100'."""
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS]


def lexical_score(query: str, text: str) -> float:
    """Fraction of the query's meaningful tokens that appear in `text`, in
    [0, 1] - the in-memory stand-in for ts_rank(..., 32)."""
    wanted = set(tokens(query))
    if not wanted:
        return 0.0
    have = set(tokens(text))
    return len(wanted & have) / len(wanted)


def is_hit(similarity: float, lexical: float) -> bool:
    return lexical > 0 or similarity >= MIN_SIMILARITY


def combined_score(
    similarity: float,
    lexical: float,
    kind: str,
    created_at: datetime,
    now: Optional[datetime] = None,
) -> float:
    score = max(similarity, 0.0) + LEXICAL_WEIGHT * lexical
    if kind == "quick":
        now = now or datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_days = max((now - created_at).total_seconds() / 86400.0, 0.0)
        score *= 0.5 + 0.5 * 0.5 ** (age_days / QUICK_HALF_LIFE_DAYS)
    return score


def snippet(text: str, query: Optional[str], preferred: Optional[str] = None) -> str:
    """The passage to show for a hit.

    `preferred` is the best-matching chunk by meaning, when there is one. If it
    contains a query token it wins; otherwise the window around the first
    token that does match is more useful than a chunk that only matched by
    meaning - it is the reason the note came back.
    """
    wanted = set(tokens(query or ""))
    if preferred and (not wanted or wanted & set(tokens(preferred))):
        return _clip(preferred)
    if wanted:
        lowered = text.lower()
        for token in wanted:
            m = re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", lowered)
            if m:
                start = max(m.start() - SNIPPET_CHARS // 3, 0)
                piece = text[start:start + SNIPPET_CHARS].strip()
                return ("…" if start > 0 else "") + piece + ("…" if start + SNIPPET_CHARS < len(text) else "")
    return _clip(preferred or text)


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= SNIPPET_CHARS else text[:SNIPPET_CHARS].rstrip() + "…"
