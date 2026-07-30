"""
consolidation/ - episodic Memory -> durable Persona  (Section 5.5 -> 5.4)

STATUS: working draft

WHAT THIS FILE IS
The pass that turns "what happened, repeatedly" into "what is true about this
user". Five navigation requests that all resolved to the same bagel shop are,
individually, five episodes; together they are one fact - "regularly goes to
Brooklyn Boy Bagels on weekday mornings" - and that fact belongs in Persona,
where a vector search can find it months later after the episodes have scrolled
out of the last-five window the Intent Surface sees.

THREE STAGES, DELIBERATELY SEPARATE

  1. count    (trends.py)  - no model. Group episodes by a resolved tool
                             parameter or event field, keep what clears
                             MIN_SUPPORT. Pure arithmetic, recomputable,
                             checkable by hand.
  2. phrase   (here)       - one Claude call turns each counted repetition into
                             a sentence and files it in the ontology. The model
                             chooses wording and category; it does not decide
                             what is or is not a trend, and it cannot invent a
                             fact that no episodes support.
  3. upsert   (here)       - write into Persona, updating the existing belief
                             in place when this trend has been seen before, so
                             re-running is idempotent rather than duplicating.

WHY THE SPLIT
Confidence comes from `support` - how many episodes - not from the model's
impression. That keeps a derived belief auditable: every fact carries the
episode ids that produced it, and you can always ask why NOVA thinks something.

WHAT DOES NOT COME HERE
Ephemeral detail. "Parked on level 3" repeated across a term is not "the user
habitually parks on level 3", it is a different car park each time and it stops
mattering the moment they drive away. Only signals named in trends.py's
COUNTED_* maps are counted, and that list is the guard - it is an allowlist of
things whose repetition means something, not a scan for anything that recurs.

USAGE
    from app.consolidation import consolidate, preview

    preview()       # what would be written, no writes
    consolidate()   # phrase and upsert into Persona

    python scripts/consolidate_memory.py run     # the same, from the CLI
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional

from .models import (
    CATEGORY_PREFERENCES,
    CATEGORY_ROUTINE_APPS,
    CATEGORY_ROUTINE_PLACES,
    CATEGORY_ROUTINE_TIMING,
    MIN_SUPPORT,
    SOURCE_STATED,
    Candidate,
    DerivedFact,
    StatedFact,
    confidence_for,
)
from .statements import find_statements
from .trends import find_candidates


# Importable both as `app.consolidation` (scripts and tests) and as bare
# `consolidation` (the server, launched as uvicorn main:app from backend/app/).
# Under the second, this package is top-level and `from .. import persona` is an
# ImportError - "beyond top-level package" - which is how a working CLI pass and
# a 500 from POST /persona/consolidate came to coexist. The store modules do the
# same dance in their own __init__ files; doing it here once keeps the call sites
# below from each repeating it.
#
# Resolved on every call rather than at import: these are cheap attribute lookups
# after the first, and keeping them lazy is what stops importing this package
# from constructing a Supabase client or loading an embedding model.
def _memory():
    try:
        from .. import memory        # app.consolidation
    except ImportError:              # pragma: no cover
        import memory                # consolidation (cwd = backend/app)
    return memory


def _persona():
    try:
        from .. import persona       # app.consolidation
    except ImportError:              # pragma: no cover
        import persona               # consolidation (cwd = backend/app)
    return persona

__all__ = [
    "consolidate",
    "consolidate_trends",
    "consolidate_statements",
    "preview",
    "preview_statements",
    "Candidate",
    "DerivedFact",
    "StatedFact",
    "find_candidates",
    "find_statements",
    "confidence_for",
    "MIN_SUPPORT",
]

# Same family as the Intent Surface. This is a batch job over a handful of
# counted candidates, not a reasoning task - it phrases and files, nothing more.
MODEL = "claude-haiku-4-5"

# A phraser turns counted candidates into filed facts. Injectable so tests (and
# scripts/consolidate_memory.py --dry-run) can run the whole pipeline without
# an API key.
Phraser = Callable[[list[Candidate]], list[DerivedFact]]

PHRASING_PROMPT = (
    "You are turning counted behavioural patterns into durable facts about one "
    "user, for a personal assistant's long-term memory.\n\n"
    "Each input is a pattern ALREADY ESTABLISHED by counting episodes - you are "
    "not deciding whether it is real, only how to say it. `support` is how many "
    "episodes back it, `hours` are the local hours-of-day they happened at, and "
    "`exemplars` are the user's own words on those occasions.\n\n"
    "For each pattern return an object with:\n"
    "  signal, value  - copied back exactly, so the result can be matched up\n"
    "  text    - one sentence stating the fact about the user, in the third "
    "person, specific enough to still make sense in six months. Include timing "
    "only if `hours` genuinely clusters. Do not include the count.\n"
    "  category - the ontology path, one of exactly: "
    "[\"routines\",\"places\"] for somewhere they repeatedly go, "
    "[\"routines\",\"timing\"] for when they repeatedly do something, "
    "[\"routines\",\"apps\"] for what they repeatedly interact with, "
    "or [\"preferences\",<domain>] for a choice made consistently.\n\n"
    "Return ONLY a JSON array of these objects, no prose."
)


def preview(min_support: int = MIN_SUPPORT,
            phraser: Optional[Phraser] = None) -> list[DerivedFact]:
    """The trend pass only: everything consolidate_trends() would write."""
    return _derive(min_support, phraser)


def consolidate_trends(min_support: int = MIN_SUPPORT,
                       phraser: Optional[Phraser] = None) -> list[DerivedFact]:
    """Count, phrase, and write derived facts into Persona."""
    facts = _derive(min_support, phraser)
    for fact in facts:
        _upsert(fact)
    print(f"[consolidation] wrote {len(facts)} derived fact(s)")
    return facts


def _forgotten_keys() -> set[str]:
    """Patterns the user has deleted. Empty if Persona is unreachable - which
    fails towards re-deriving rather than towards writing nothing, the same way
    every other store read here degrades."""
    try:
        return _persona().forgotten()
    except Exception as e:
        print(f"[consolidation] forgotten list unavailable: {e}")
        return set()


def _key_of(candidate: Candidate) -> str:
    """The tombstone key a fact derived from this candidate would carry."""
    key = _persona().tombstone_key(
        {"signal": candidate.signal, "value": candidate.value}
    )
    return key or ""


def _pending_statements(extractor: Optional[Any] = None) -> list[StatedFact]:
    """Statements not yet held, deduplicated. Shared so `preview` shows exactly
    what `run` would write rather than an optimistic version of it."""
    held = _persona().all_facts()
    # A deleted statement is an episode that must never be re-read. Folding the
    # tombstones in with the already-extracted ids means one rule covers both:
    # "we have dealt with this utterance", whether the answer was kept or thrown
    # away. Without it, deleting a stated fact removes the only record that its
    # episode was ever read, and the next run extracts it again.
    seen = _extracted_episode_ids(held) | {
        key.removeprefix("episode:")
        for key in _forgotten_keys()
        if key.startswith("episode:")
    }
    return find_statements(
        _episodes(),
        seen_episode_ids=seen,
        extractor=extractor,
        existing_texts=[f.text for f in held],
    )


def preview_statements(extractor: Optional[Any] = None) -> list[StatedFact]:
    """The statement pass only, without writing."""
    return _pending_statements(extractor)


def consolidate_statements(extractor: Optional[Any] = None) -> list[StatedFact]:
    """Read what the user said about themselves and write it into Persona.

    Separate from the trend pass on purpose - see statements.py. A statement
    needs no repetition to count, so this does not take min_support.
    """
    facts = _pending_statements(extractor)
    for fact in facts:
        _upsert_stated(fact)
    print(f"[consolidation] wrote {len(facts)} stated fact(s)")
    return facts


def consolidate(min_support: int = MIN_SUPPORT,
                phraser: Optional[Phraser] = None,
                extractor: Optional[Any] = None) -> dict[str, list[Any]]:
    """Both passes. Returns {"derived": [...], "stated": [...]}."""
    return {
        "derived": consolidate_trends(min_support, phraser),
        "stated": consolidate_statements(extractor),
    }


def _derive(min_support: int, phraser: Optional[Phraser]) -> list[DerivedFact]:
    """Count, then phrase, then check the phrasing against the counting.

    The check is here rather than inside the phraser because it is a property
    of the pass, not of one implementation of it: whatever produces the wording
    - Claude, a template, a test double - a fact is only allowed out if the
    counting stage actually produced its candidate. That is what makes
    `support` and `episode_ids` mean anything. Without it, a phraser that
    hallucinated "hates bagels" would get it written to Persona wearing the
    evidence of a trend about somewhere they go.
    """
    candidates = find_candidates(_episodes(), min_support=min_support)
    print(f"[consolidation] {len(candidates)} candidate(s) at support>={min_support}")

    # A pattern the user deleted from the Knowledge Map stays deleted, however
    # many more times they do the thing. Dropped here rather than at the upsert
    # so a forgotten trend is not even sent to the phrasing model: there is no
    # point paying to word a fact that will never be written.
    forgotten = _forgotten_keys()
    kept_candidates = [c for c in candidates if _key_of(c) not in forgotten]
    if len(kept_candidates) != len(candidates):
        print(f"[consolidation] {len(candidates) - len(kept_candidates)} candidate(s) "
              f"skipped - previously deleted by the user")
    candidates = kept_candidates
    if not candidates:
        return []

    counted = {(c.signal, c.value) for c in candidates}
    kept: list[DerivedFact] = []
    for fact in (phraser or _claude_phraser)(candidates):
        key = (fact.candidate.signal, fact.candidate.value)
        if key not in counted:
            print(f"[consolidation] dropped unsupported fact: {fact.text!r} {key}")
            continue
        if not fact.text.strip():
            continue
        kept.append(fact)
    return kept


# --- stage 1: read -----------------------------------------------------------

def _episodes() -> list[dict[str, Any]]:
    """Every episode, oldest first.

    Deliberately the whole log: a trend is a property of the history, and the
    last-N window the Intent Surface reads is exactly what this pass exists to
    see past. It is a batch job run occasionally, so the cost is acceptable -
    but it is the reason this does not belong in the request path.
    """
    rows = _memory().all()
    print(f"[consolidation] read {len(rows)} episode(s)")
    return rows


# --- stage 2: phrase ---------------------------------------------------------

def _claude_phraser(candidates: list[Candidate]) -> list[DerivedFact]:
    """One Claude call for the whole batch, matched back by (signal, value).

    Anything the model returns that does not correspond to a candidate is
    dropped: it cannot introduce a fact that no episodes support.
    """
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    payload = [
        {
            "signal": c.signal,
            "value": c.value,
            "support": c.support,
            "span_days": c.span_days,
            "hours": c.hours,
            "exemplars": c.exemplars,
        }
        for c in candidates
    ]

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=PHRASING_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload)}],
    )
    text = "".join(b.text for b in response.content if b.type == "text")

    by_key = {(c.signal, c.value): c for c in candidates}
    facts: list[DerivedFact] = []
    for item in _parse_json_array(text):
        candidate = by_key.get((item.get("signal"), item.get("value")))
        if candidate is None:
            print(f"[consolidation] dropped unmatched phrasing: {item!r}")
            continue
        facts.append(DerivedFact(
            text=str(item.get("text") or "").strip(),
            category=_valid_category(item.get("category")),
            candidate=candidate,
        ))
    return [f for f in facts if f.text]


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    """Parse the model's reply, tolerating a ```json fence around it."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        cleaned = cleaned[4:] if cleaned.startswith("json") else cleaned
    try:
        parsed = json.loads(cleaned.strip())
    except json.JSONDecodeError as e:
        print(f"[consolidation] could not parse phrasing reply: {e}")
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


_ALLOWED_ROOTS = (
    CATEGORY_ROUTINE_PLACES,
    CATEGORY_ROUTINE_TIMING,
    CATEGORY_ROUTINE_APPS,
)


def _valid_category(category: Any) -> list[str]:
    """Hold the model to the ontology. An unrecognised path is not a reason to
    drop the fact, but it is a reason not to trust the filing - those land under
    ["routines"] where a category-filtered search can still reach them."""
    if not isinstance(category, list) or not all(isinstance(c, str) for c in category):
        return ["routines"]
    if list(category) in [list(r) for r in _ALLOWED_ROOTS]:
        return list(category)
    if category[:1] == CATEGORY_PREFERENCES and len(category) >= 2:
        return list(category[:2])
    return ["routines"]


# --- stage 3: upsert ---------------------------------------------------------

def _extracted_episode_ids(held: list[Any]) -> set[str]:
    """Episodes the statement pass has already read, including the ones folded
    in by deduplication (`also_from`) - those were read too, and re-offering
    them would pay the model to rediscover a fact that was merged away.

    Exact enumeration, not a similarity search: this decides whether an
    utterance gets re-sent, and a near-miss means either paying twice or
    writing the same belief twice. Unlike a derived fact there is no
    (signal, value) identity to match a re-extracted statement against, so the
    episode id is the only thing that makes this idempotent.
    """
    ids: set[str] = set()
    for fact in held:
        meta = fact.metadata or {}
        if meta.get("source") != SOURCE_STATED:
            continue
        if meta.get("episode_id"):
            ids.add(str(meta["episode_id"]))
        ids.update(str(i) for i in (meta.get("also_from") or []))
    print(f"[statements] {len(ids)} episode(s) already extracted")
    return ids


def _upsert_stated(fact: StatedFact) -> str:
    """Write a stated fact into Persona.

    Always an insert. A statement is tied to the moment it was said, and two
    statements weeks apart are two facts even when they contradict - resolving
    that is the Persona correction loop's job (Section 5.5 done-when (b)), not
    this pass's. Overwriting here would quietly destroy the earlier one.
    """
    persona = _persona()
    fact_id = persona.upsert(persona.Fact(
        text=fact.text,
        category=fact.category,
        confidence=fact.confidence,
        metadata=fact.evidence(),
    ))
    print(f"[statements] added {fact_id}: {fact.text!r} <- {fact.quote[:50]!r}")
    return fact_id


def _upsert(fact: DerivedFact) -> str:
    """Write into Persona, updating in place if this trend is already held.

    Idempotence matters more than it looks: this runs repeatedly over a growing
    log, so the same habit is re-derived every time with a higher `support`. It
    should sharpen one belief, not accumulate near-duplicates of it.
    """
    persona = _persona()
    existing = _existing_id(persona, fact)
    fact_id = persona.upsert(persona.Fact(
        id=existing,
        text=fact.text,
        category=fact.category,
        confidence=fact.confidence,
        metadata=fact.candidate.evidence(),
    ))
    verb = "updated" if existing else "added"
    print(f"[consolidation] {verb} {fact_id}: {fact.text!r} "
          f"(support={fact.candidate.support}, confidence={fact.confidence})")
    return fact_id


def _existing_id(persona: Any, fact: DerivedFact) -> Optional[str]:
    """The id of the belief already holding this trend, if there is one.

    Matched on (signal, value) in metadata - the identity of the pattern, not
    the wording, which the phrasing pass may render differently each run. The
    lookup is similarity-first because that is what the store seam offers, so
    it searches on both the phrasing and the raw value to give the right row
    two chances to surface.
    """
    signal = fact.candidate.signal
    value = fact.candidate.value

    for query in (fact.text, value):
        try:
            matches = persona.search(persona.PersonaQuery(text=query, limit=20))
        except Exception as e:
            print(f"[consolidation] persona lookup skipped: {e}")
            return None
        for match in matches:
            meta = match.fact.metadata or {}
            if meta.get("signal") == signal and meta.get("value") == value:
                return match.fact.id
    return None
