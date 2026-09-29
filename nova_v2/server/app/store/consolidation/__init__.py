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
  3. upsert   (here)       - write through persona.remember(), updating the
                             existing belief in place when this trend has been
                             seen before (so re-running is idempotent), and
                             otherwise merging it into a belief that says the
                             same, or resolving a contradiction by recency -
                             a trend's time is its last supporting episode.

A trend already held is not re-phrased: its wording is kept (the user may have
edited it) and only its evidence moves. A trend a newer belief superseded is
not even counted back in unless an episode newer than that belief supports it.

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
    from app.store.consolidation import consolidate, preview

    preview(user_id)       # what would be written, no writes
    consolidate(user_id)   # phrase and upsert into Persona

WHOSE HISTORY
One account's at a time. Every entry point
takes the user first, reads only their episodes, facts and tombstones, and
writes only into their Persona - otherwise a habit counted out of one user's
trips would be filed as a fact about another.

    POST /persona/consolidate[?preview=true]      # the same, over HTTP
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Union
from uuid import UUID

from app.store.consolidation.models import (
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
from app.store.consolidation.statements import find_statements
from app.store.consolidation.trends import find_candidates


# Resolved on every call rather than imported at module level: these are cheap
# attribute lookups after the first, and keeping them lazy is what stops
# importing this package from constructing a Supabase client or loading an
# embedding model just because something imported app.store.consolidation.
def _memory():
    from app.store import memory
    return memory


def _persona():
    from app.store import persona
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

UserId = Union[UUID, str]

# Same family as the Intent Surface. This is a batch job over a handful of
# counted candidates, not a reasoning task - it phrases and files, nothing more.
MODEL = "claude-haiku-4-5"

# A phraser turns counted candidates into filed facts. Injectable so tests can run the whole pipeline without an API key.
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


def preview(user_id: UserId, min_support: int = MIN_SUPPORT,
            phraser: Optional[Phraser] = None) -> list[DerivedFact]:
    """The trend pass only: everything consolidate_trends() would write."""
    return _derive(user_id, min_support, phraser)


def consolidate_trends(user_id: UserId, min_support: int = MIN_SUPPORT,
                       phraser: Optional[Phraser] = None) -> list[DerivedFact]:
    """Count, phrase, and write derived facts into this user's Persona."""
    facts = _derive(user_id, min_support, phraser)
    for fact in sorted(facts, key=lambda f: _time(f.candidate.last_seen) or _EPOCH):
        fact.outcome = _upsert(user_id, fact).action.value
    print(f"[consolidation] wrote {len(facts)} derived fact(s)")
    return facts


def _forgotten_keys(user_id: UserId) -> set[str]:
    """Patterns the user has deleted. Empty if Persona is unreachable - which
    fails towards re-deriving rather than towards writing nothing, the same way
    every other store read here degrades."""
    try:
        return _persona().forgotten(user_id)
    except Exception as e:
        print(f"[consolidation] forgotten list unavailable: {e}")
        return set()


def _superseded_keys(user_id: UserId) -> dict[str, datetime]:
    """Patterns a newer belief won over, and when. Empty if unreachable, as
    _forgotten_keys."""
    try:
        return _persona().superseded(user_id)
    except Exception as e:
        print(f"[consolidation] superseded list unavailable: {e}")
        return {}


def _key_of(candidate: Candidate) -> str:
    """The tombstone key a fact derived from this candidate would carry."""
    key = _persona().tombstone_key(
        {"signal": candidate.signal, "value": candidate.value}
    )
    return key or ""


def _pending_statements(user_id: UserId, extractor: Optional[Any] = None) -> list[StatedFact]:
    """Statements not yet extracted, deduplicated. Shared so `preview` shows
    exactly what `run` would write rather than an optimistic version of it."""
    held = _persona().all_facts(user_id)
    # A deleted statement is an episode that must never be re-read. Folding the
    # tombstones in with the already-extracted ids means one rule covers both:
    # "we have dealt with this utterance", whether the answer was kept or thrown
    # away. Without it, deleting a stated fact removes the only record that its
    # episode was ever read, and the next run extracts it again. A superseded
    # statement is the same: something newer contradicted it, and an episode
    # never gets newer, so it has nothing more to say.
    dealt_with = set(_forgotten_keys(user_id)) | set(_superseded_keys(user_id))
    seen = _extracted_episode_ids(held) | {
        key.removeprefix("episode:") for key in dealt_with if key.startswith("episode:")
    }
    return find_statements(_episodes(user_id), seen_episode_ids=seen, extractor=extractor)


def preview_statements(user_id: UserId, extractor: Optional[Any] = None) -> list[StatedFact]:
    """The statement pass only, without writing."""
    return _pending_statements(user_id, extractor)


def consolidate_statements(user_id: UserId, extractor: Optional[Any] = None) -> list[StatedFact]:
    """Read what the user said about themselves and write it into Persona.

    Separate from the trend pass on purpose - see statements.py. A statement
    needs no repetition to count, so this does not take min_support.
    """
    facts = _pending_statements(user_id, extractor)
    # Oldest first, so within one run the later statement is the one standing
    # at the end - the same outcome as if each had been saved as it was said.
    for fact in sorted(facts, key=lambda f: f.stated_at or _EPOCH):
        fact.outcome = _upsert_stated(user_id, fact).action.value
    print(f"[consolidation] wrote {len(facts)} stated fact(s)")
    return facts


def consolidate(user_id: UserId,
                min_support: int = MIN_SUPPORT,
                phraser: Optional[Phraser] = None,
                extractor: Optional[Any] = None) -> dict[str, list[Any]]:
    """Both passes, for one user, then a sweep of anything written while the
    judge was unavailable. Returns {"derived": [...], "stated": [...],
    "reconciled": [...]}."""
    derived = consolidate_trends(user_id, min_support, phraser)
    stated = consolidate_statements(user_id, extractor)
    try:
        reconciled = _persona().reconcile_all(user_id, only_unreconciled=True)
    except Exception as e:
        print(f"[consolidation] reconcile sweep skipped: {e}")
        reconciled = []
    return {"derived": derived, "stated": stated, "reconciled": reconciled}


def _derive(user_id: UserId, min_support: int, phraser: Optional[Phraser]) -> list[DerivedFact]:
    """Count, then phrase, then check the phrasing against the counting.

    The check is here rather than inside the phraser because it is a property
    of the pass, not of one implementation of it: whatever produces the wording
    - Claude, a template, a test double - a fact is only allowed out if the
    counting stage actually produced its candidate. That is what makes
    `support` and `episode_ids` mean anything. Without it, a phraser that
    hallucinated "hates bagels" would get it written to Persona wearing the
    evidence of a trend about somewhere they go.
    """
    candidates = find_candidates(_episodes(user_id), min_support=min_support)
    print(f"[consolidation] {len(candidates)} candidate(s) at support>={min_support}")

    # A pattern the user deleted from the Knowledge Map stays deleted, however
    # many more times they do the thing. Dropped here rather than at the upsert
    # so a forgotten trend is not even sent to the phrasing model: there is no
    # point paying to word a fact that will never be written.
    forgotten = _forgotten_keys(user_id)
    kept_candidates = [c for c in candidates if _key_of(c) not in forgotten]
    if len(kept_candidates) != len(candidates):
        print(f"[consolidation] {len(candidates) - len(kept_candidates)} candidate(s) "
              f"skipped - previously deleted by the user")
    candidates = kept_candidates

    # A newer belief contradicted this pattern. It may come back, but only on
    # evidence newer than that belief - otherwise it would lose again, and
    # pay for the phrasing to do it.
    superseded = _superseded_keys(user_id)
    candidates = [c for c in candidates if not _still_superseded(c, superseded)]
    if not candidates:
        return []

    # Already held: keep the belief's wording (the user may have edited it) and
    # just carry the new evidence. Only patterns new to Persona get phrased.
    held = _held_by_key(user_id)
    known: list[DerivedFact] = []
    fresh: list[Candidate] = []
    for c in candidates:
        holder = held.get(_key_of(c))
        if holder is not None:
            known.append(DerivedFact(text=holder.text, category=holder.category, candidate=c))
        else:
            fresh.append(c)
    if not fresh:
        return known

    counted = {(c.signal, c.value) for c in fresh}
    kept: list[DerivedFact] = list(known)
    for fact in (phraser or _claude_phraser)(fresh):
        key = (fact.candidate.signal, fact.candidate.value)
        if key not in counted:
            print(f"[consolidation] dropped unsupported fact: {fact.text!r} {key}")
            continue
        if not fact.text.strip():
            continue
        kept.append(fact)
    return kept


def _still_superseded(candidate: Candidate, superseded: dict[str, datetime]) -> bool:
    lost_at = superseded.get(_key_of(candidate))
    if lost_at is None:
        return False
    last_seen = _time(candidate.last_seen)
    return last_seen is None or last_seen <= lost_at


def _held_by_key(user_id: UserId) -> dict[str, Any]:
    """Each pattern key -> the belief standing for it. The belief written as
    that pattern is preferred over one it was merged into."""
    persona = _persona()
    held: dict[str, Any] = {}
    try:
        facts = persona.all_facts(user_id)
    except Exception as e:
        print(f"[consolidation] persona read skipped: {e}")
        return held
    for fact in facts:
        primary = persona.tombstone_key(fact.metadata or {})
        for key in persona.source_keys(fact.metadata or {}):
            if key == primary or key not in held:
                held[key] = fact
    return held


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _time(value: Any) -> Optional[datetime]:
    """An ISO timestamp from the log as an aware datetime, or None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --- stage 1: read -----------------------------------------------------------

def _episodes(user_id: UserId) -> list[dict[str, Any]]:
    """Every one of this user's episodes, oldest first.

    Deliberately the whole log: a trend is a property of the history, and the
    last-N window the Intent Surface reads is exactly what this pass exists to
    see past. It is a batch job run occasionally, so the cost is acceptable -
    but it is the reason this does not belong in the request path.
    """
    rows = _memory().all(user_id)
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
    # Every belief, not just stated ones: a statement merged into a derived
    # belief as a duplicate leaves its episode in that belief's provenance.
    for fact in held:
        for key in _persona().source_keys(fact.metadata or {}):
            if key.startswith("episode:"):
                ids.add(key.removeprefix("episode:"))
    print(f"[statements] {len(ids)} episode(s) already extracted")
    return ids


def _upsert_stated(user_id: UserId, fact: StatedFact) -> Any:
    """Write a stated fact through persona.remember(), dated when it was said.

    A statement is tied to the moment it was said. If a held belief says the
    same, they merge; if one contradicts it, whichever was said later stands -
    so an old utterance read today loses to a newer one, rather than quietly
    overwriting it the way an in-place upsert would.
    """
    persona = _persona()
    result = persona.remember(user_id, persona.Fact(
        text=fact.text,
        category=fact.category,
        confidence=fact.confidence,
        metadata=fact.evidence(),
    ), stated_at=fact.stated_at)
    print(f"[statements] {result.action.value} {result.fact_id}: {fact.text!r} "
          f"<- {fact.quote[:50]!r}")
    return result


def _upsert(user_id: UserId, fact: DerivedFact) -> Any:
    """Write a trend through persona.remember(), dated by its last episode.

    Idempotence matters more than it looks: this runs repeatedly over a growing
    log, so the same habit is re-derived every time with a higher `support`. It
    should sharpen one belief, not accumulate near-duplicates of it - so the
    belief already standing for this (signal, value) is updated in place, found
    by its pattern key rather than by similarity to whatever the wording is.
    """
    persona = _persona()
    key = _key_of(fact.candidate)
    last_seen = _time(fact.candidate.last_seen)
    holders = persona.find_by_source_key(user_id, key)
    primary = next((h for h in holders if persona.tombstone_key(h.metadata or {}) == key), None)

    if primary is not None:
        # The belief this trend was written as. New evidence, same belief; an
        # edit the user made keeps its wording and stays theirs.
        meta = {**(primary.metadata or {}), **fact.candidate.evidence()}
        if (primary.metadata or {}).get("edited"):
            meta["source"] = SOURCE_STATED
        written = primary.model_copy(update={
            "confidence": fact.confidence, "metadata": meta, "stated_at": None,
        })
        when = max(filter(None, [last_seen, primary.stated_at]), default=None)
    elif holders:
        # Merged into another belief as a duplicate: that belief now also rests
        # on this trend, so the trend's newest episode is its newest evidence.
        holder = holders[0]
        written = holder.model_copy(update={"stated_at": None})
        when = max(filter(None, [last_seen, holder.stated_at]), default=None)
    else:
        written = persona.Fact(
            text=fact.text,
            category=fact.category,
            confidence=fact.confidence,
            metadata=fact.candidate.evidence(),
        )
        when = last_seen

    result = persona.remember(user_id, written, stated_at=when)
    print(f"[consolidation] {result.action.value} {result.fact_id}: {fact.text!r} "
          f"(support={fact.candidate.support}, confidence={fact.confidence})")
    return result
