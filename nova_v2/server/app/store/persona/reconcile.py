"""remember(): the one way a belief gets into Persona.

THE INVARIANT
Two beliefs that say the same thing never coexist, and two that cannot both
be true never coexist. "Likes apples" and "Is fond of apples" become one fact;
"Likes apples" and "Doesn't like apples" become whichever was said last.

Every writer - the memory tool, both consolidation passes, note promotion,
onboarding, Knowledge Map edits - goes through remember(), so the invariant
holds however a belief arrived. persona.upsert() is still there as the raw
primitive, and checks nothing.

HOW, CHEAPEST FIRST
  1. Exact duplicate. The normalised text hashes the same as a held fact
     (content_hash, era-memory's idea): merge. No embedding, no model.
  2. Neighbours. Embed once, hybrid-search every category (a contradiction can
     be filed anywhere - "likes coffee" under opinions, "has given up coffee"
     under routines). Nothing close enough: insert. No model. Most new facts
     about new topics stop here.
  3. Judge. One model call labels each neighbour duplicate / contradicts /
     unrelated (judge.py). The judge sees texts only.
  4. Resolve, in code, by `stated_at` - most recent wins, whatever the source:
       - a contradicting belief is newer: the new one is REJECTED.
       - duplicates: merged into one fact, keeping the held wording and id and
         the union of their provenance.
       - the new one is newer than every contradiction: it overwrites the
         closest one IN PLACE - same id, so the Knowledge Map node stays put -
         and any other contradictions are removed. REPLACED.

SUPERSEDED IS NOT FORGOTTEN
A belief that loses leaves a watermark (store.supersede), not a tombstone:
"a newer belief won over this pattern as of T". Consolidation may offer that
pattern again only with evidence newer than T. An old episode never gets
newer, so it stays down; a trend the user started doing again can win back.
Tombstones stay what they were - the user saying "forget this".

FAILURE
The user's save is never lost to the machinery. A judge that can't answer, or
a lock that can't be had, writes the fact marked `unreconciled`; the next
sweep (reconcile_all, run at the end of every consolidation and in the
background after a voice save) resolves it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

from app.store.persona.judge import Judge, JudgeUnavailable
from app.store.persona.models import Fact, Match, content_hash, source_keys
from app.store.persona.store import (
    DuplicateHash,
    FactNotFound,
    InMemoryPersonaStore,
    PersonaStore,
    UserId,
)

# Which neighbours go to the judge. Measured fact-to-fact with bge-large
# (graph.py): related facts 0.70-0.82, unrelated <= 0.55, so 0.60 keeps every
# plausible pair and drops the noise. A shared keyword ("COMP2100", "apples")
# earns a second chance down to 0.45. Unmeasured for negation pairs
# specifically - every verdict is logged with both scores; tune from those.
CANDIDATE_MIN_SIMILARITY = 0.60
LEXICAL_CANDIDATE_MIN_SIMILARITY = 0.45
CANDIDATE_LIMIT = 8
# The voice path waits on the judge; fewer neighbours is a shorter call.
VOICE_CANDIDATE_LIMIT = 5

UNRECONCILED = "unreconciled"
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


class RememberAction(str, Enum):
    ADDED = "added"          # a new belief
    UPDATED = "updated"      # the targeted belief rewritten, nothing else touched
    MERGED = "merged"        # it duplicated a held belief; one fact remains
    REPLACED = "replaced"    # it contradicted older beliefs, which are gone
    REJECTED = "rejected"    # a held belief contradicts it and is newer


class Superseded(BaseModel):
    id: str
    text: str
    stated_at: Optional[datetime] = None


class RememberResult(BaseModel):
    action: RememberAction
    # The surviving belief - the new one, the one it merged into, or (when
    # REJECTED) the newer belief that beat it.
    fact_id: Optional[str] = None
    superseded: list[Superseded] = Field(default_factory=list)
    merged_ids: list[str] = Field(default_factory=list)
    judged: bool = False
    unreconciled: bool = False


def remember(
    user_id: UserId,
    fact: Fact,
    *,
    stated_at: Optional[datetime] = None,
    judge: Optional[Judge] = None,
    candidate_limit: int = CANDIDATE_LIMIT,
    store: Optional[PersonaStore] = None,
) -> RememberResult:
    """Write a belief without breaking the invariant. See the module docstring.

    `stated_at` is when it was said or last seen (defaults to fact.stated_at,
    then now). `fact.id` makes it an in-place write to that belief - a
    Knowledge Map edit, a trend refresh, an onboarding answer changing - which
    survives with the same id unless something newer contradicts it.
    FactNotFound if that id isn't this user's.
    """
    from app.store import persona

    store = store or persona.get_store()
    judge = judge or persona.get_judge()
    when = _aware(stated_at or fact.stated_at or datetime.now(timezone.utc))
    fact = fact.model_copy(update={"stated_at": when})

    with store.lock(user_id) as held:
        if not held:
            print(f"[reconcile] Persona busy - writing {fact.text!r} unreconciled")
            result = _write_unreconciled(store, user_id, fact, None)
        else:
            result = _reconcile(store, user_id, fact, judge, candidate_limit)
        _place_in_group(store, user_id, result, fact.text)
        return result


def _place_in_group(store: PersonaStore, user_id: UserId, result: RememberResult, text: str) -> None:
    """Put a written belief in its meaning group (clusters.py) straight away, so
    the map shows it under a heading. Best-effort: anything missed is grouped
    on the next consolidation pass. A merge leaves the kept belief's group as it
    was; a rejection wrote nothing."""
    if result.action in (RememberAction.MERGED, RememberAction.REJECTED) or not result.fact_id:
        return
    from app.store import persona
    from app.store.persona.clusters import place

    groups = persona.get_cluster_store()
    if groups is None:
        return
    try:
        place(groups, store, user_id, result.fact_id, text)
    except Exception as e:
        persona.clusters_failed(e)


def reconcile_all(
    user_id: UserId,
    *,
    judge: Optional[Judge] = None,
    only_unreconciled: bool = False,
    dry_run: bool = False,
    store: Optional[PersonaStore] = None,
) -> list[RememberResult]:
    """Bring a whole Persona in line with the invariant.

    Replays every belief oldest `stated_at` first through the same rules as
    remember(), each as the target of its own write, so the survivors are
    exactly what remember() would have kept had it existed all along. With
    `only_unreconciled`, just the beliefs written while the judge or lock was
    unavailable. `dry_run` runs the same sweep over an in-memory copy and
    returns what it would do. Returns only the beliefs something happened to.
    """
    from app.store import persona

    real = store or persona.get_store()
    judge = judge or persona.get_judge()
    if dry_run:
        work: PersonaStore = InMemoryPersonaStore(_StoreEmbedder(real))
        work.load(user_id, real.all_facts(user_id), real.vectors(user_id), real.superseded(user_id))
    else:
        work = real
        try:
            filled = work.backfill_keys(user_id)
            if filled:
                print(f"[reconcile] backfilled hash/keys on {filled} fact(s)")
        except Exception as e:
            print(f"[reconcile] backfill skipped: {e}")

    facts = work.all_facts(user_id)
    if only_unreconciled:
        facts = [f for f in facts if (f.metadata or {}).get(UNRECONCILED)]
    facts.sort(key=_when)
    vectors = work.vectors(user_id) if facts else {}

    results: list[RememberResult] = []
    for fact in facts:
        with work.lock(user_id) as held:
            if not held:
                print("[reconcile] Persona busy - sweep stopped early")
                break
            try:
                current = work.get(user_id, fact.id)
            except FactNotFound:
                continue  # merged or replaced earlier in this sweep
            result = _reconcile(work, user_id, current, judge, CANDIDATE_LIMIT,
                                sweep=True, vector=vectors.get(fact.id))
        if result.action != RememberAction.UPDATED or result.unreconciled:
            results.append(result)

    print(f"[reconcile] {'preview' if dry_run else 'sweep'} over {len(facts)} fact(s): "
          f"{len(results)} changed")
    return results


def merge_provenance(keeper: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    """`keeper`'s metadata plus everything that could regenerate `other`:
    episodes into `also_from` (what consolidation reads to know an utterance
    was already extracted), every other key into `also_keys`. So the merged
    fact keeps consolidation idempotent, and deleting it tombstones all of it."""
    out = dict(keeper)
    own = set(source_keys(out))
    also_from = list(out.get("also_from") or [])
    also_keys = list(out.get("also_keys") or [])
    for key in source_keys(other):
        if key in own:
            continue
        own.add(key)
        if key.startswith("episode:"):
            also_from.append(key.removeprefix("episode:"))
        else:
            also_keys.append(key)
    if also_from:
        out["also_from"] = also_from
    if also_keys:
        out["also_keys"] = also_keys
    return out


# --- the algorithm ---------------------------------------------------------------

def _reconcile(store: PersonaStore, user_id: UserId, new: Fact, judge: Judge, limit: int, *,
               sweep: bool = False, vector: Optional[list[float]] = None) -> RememberResult:
    """_reconcile_once, retried once if a concurrent write of the same text
    beat us to the unique hash index - the retry then finds it as a duplicate."""
    try:
        return _reconcile_once(store, user_id, new, judge, limit, sweep=sweep, vector=vector)
    except DuplicateHash:
        return _reconcile_once(store, user_id, new, judge, limit, sweep=sweep, vector=vector)


def _reconcile_once(store: PersonaStore, user_id: UserId, new: Fact, judge: Judge, limit: int, *,
                    sweep: bool, vector: Optional[list[float]]) -> RememberResult:
    target = store.get(user_id, new.id) if new.id else None
    new = _clean(new)
    digest = content_hash(new.text)

    # An in-place write that doesn't change what the belief says (a refiling,
    # a trend's support ticking up) can't create a duplicate or contradiction
    # that wasn't already there. Skipped in a sweep, whose job is to look.
    if target is not None and not sweep and content_hash(target.text) == digest:
        return RememberResult(action=RememberAction.UPDATED, fact_id=store.upsert(user_id, new))

    same = [f for f in store.find_by_hash(user_id, digest) if f.id != new.id]
    if same:
        return _apply(store, user_id, new, target, dups=same, contras=[], vector=None, judged=False)

    vector = vector or store.embed(new.text)
    pool = store.candidates(user_id, new.text, vector, limit=limit * 2,
                            exclude_ids=[new.id] if new.id else [])
    near = sorted((m for m in pool if _is_candidate(m)), key=lambda m: m.similarity,
                  reverse=True)[:limit]
    if not near:
        return _write(store, user_id, new, target, vector, judged=False, sweep=sweep)

    try:
        relations = judge(new.text, [m.fact.text for m in near])
        if len(relations) != len(near):
            raise JudgeUnavailable(f"{len(relations)} verdicts for {len(near)} statements")
    except JudgeUnavailable as e:
        print(f"[reconcile] judge unavailable ({e}) - {new.text!r} written unreconciled")
        if sweep:
            return RememberResult(action=RememberAction.UPDATED, fact_id=new.id, unreconciled=True)
        return _write_unreconciled(store, user_id, new, vector)

    for m, relation in zip(near, relations):
        print(f"[reconcile] {relation:<11} cos={m.similarity:.3f} lex={m.lexical:.3f} "
              f"{new.text!r} vs {m.fact.text!r}")
    dups = [m.fact for m, r in zip(near, relations) if r == "duplicate"]
    contras = [m.fact for m, r in zip(near, relations) if r == "contradicts"]
    if not dups and not contras:
        return _write(store, user_id, new, target, vector, judged=True, sweep=sweep)
    return _apply(store, user_id, new, target, dups=dups, contras=contras, vector=vector, judged=True)


def _apply(store: PersonaStore, user_id: UserId, new: Fact, target: Optional[Fact], *,
           dups: list[Fact], contras: list[Fact], vector: Optional[list[float]],
           judged: bool) -> RememberResult:
    """Resolve duplicates and contradictions by recency. `contras` arrive
    closest-first."""
    when = _when(new)

    # A held belief contradicts this one and is newer: this one loses.
    newer = [c for c in contras if not _beats(new, c)]
    if newer:
        winner = max(newer, key=_when)
        lost: list[Superseded] = []
        _supersede(store, user_id, new.metadata, _when(winner), winner.id)
        if target is not None:
            _supersede(store, user_id, target.metadata, _when(winner), winner.id)
            store.remove(user_id, target.id)
            lost.append(_superseded(target))
        print(f"[reconcile] rejected {new.text!r}: {winner.text!r} is newer")
        return RememberResult(action=RememberAction.REJECTED, fact_id=winner.id,
                              superseded=lost, judged=judged)

    if target is not None:
        keeper, keep_new_body = target, True
    elif dups:
        keeper, keep_new_body = _pick_keeper(dups), False
    else:
        keeper, keep_new_body = contras[0], True

    if keep_new_body:
        body = new.model_copy(update={"id": keeper.id})
    else:
        body = keeper.model_copy(update={"metadata": merge_provenance(keeper.metadata, new.metadata)})

    # Everything else goes. Removed before the keeper is written so the
    # keeper's text never collides with a row that is on its way out.
    merged: list[str] = []
    for dup in dups:
        if dup.id == keeper.id:
            continue
        body = body.model_copy(update={"metadata": merge_provenance(body.metadata, dup.metadata)})
        store.remove(user_id, dup.id)
        merged.append(dup.id)

    # A contradiction overwritten in place (the keeper) or removed: either
    # way its patterns are watermarked, except any the winner itself holds.
    winning_keys = set(source_keys(body.metadata))
    superseded: list[Superseded] = []
    for contra in contras:
        _supersede(store, user_id, contra.metadata, when, keeper.id, keep=winning_keys)
        superseded.append(_superseded(contra))
        if contra.id != keeper.id:
            store.remove(user_id, contra.id)

    # Contradictions are older by now; duplicates may not be.
    stated = max([when, *(_when(d) for d in dups)])
    body = _clean(body.model_copy(update={"stated_at": stated}))
    embedding = vector if body.text == new.text else None
    fact_id = store.upsert(user_id, body, embedding=embedding)

    if contras:
        action = RememberAction.REPLACED
    elif dups:
        action = RememberAction.MERGED
    else:
        action = RememberAction.UPDATED
    print(f"[reconcile] {action.value} {fact_id}: {body.text!r}"
          + (f" (was {[s.text for s in superseded]!r})" if superseded else ""))
    return RememberResult(action=action, fact_id=fact_id, superseded=superseded,
                          merged_ids=merged, judged=judged)


def _write(store: PersonaStore, user_id: UserId, new: Fact, target: Optional[Fact],
           vector: Optional[list[float]], *, judged: bool, sweep: bool) -> RememberResult:
    """Nothing to reconcile against: write it as it is."""
    if sweep and target is not None and not (target.metadata or {}).get(UNRECONCILED):
        # A sweep that found nothing leaves a clean belief untouched rather
        # than re-writing (and re-stamping) every row it looked at.
        return RememberResult(action=RememberAction.UPDATED, fact_id=target.id, judged=judged)
    fact_id = store.upsert(user_id, _clean(new), embedding=vector)
    action = RememberAction.UPDATED if target is not None else RememberAction.ADDED
    return RememberResult(action=action, fact_id=fact_id, judged=judged)


def _write_unreconciled(store: PersonaStore, user_id: UserId, new: Fact,
                        vector: Optional[list[float]]) -> RememberResult:
    marked = new.model_copy(update={"metadata": {**(new.metadata or {}), UNRECONCILED: True}})
    fact_id = store.upsert(user_id, marked, embedding=vector)
    action = RememberAction.UPDATED if new.id else RememberAction.ADDED
    return RememberResult(action=action, fact_id=fact_id, unreconciled=True)


# --- helpers ------------------------------------------------------------------------

def _is_candidate(m: Match) -> bool:
    if m.similarity >= CANDIDATE_MIN_SIMILARITY:
        return True
    return m.lexical > 0 and m.similarity >= LEXICAL_CANDIDATE_MIN_SIMILARITY


def _beats(new: Fact, old: Fact) -> bool:
    """True if `new` wins against `old` under most-recent-wins. A tie goes to
    the newcomer, unless the held belief is one the user wrote themselves."""
    a, b = _when(new), _when(old)
    if a != b:
        return a > b
    return not (old.metadata or {}).get("edited") or bool((new.metadata or {}).get("edited"))


def _pick_keeper(dups: list[Fact]) -> Fact:
    """Which of several duplicates survives: the user's own wording first,
    then the most recently asserted."""
    return max(dups, key=lambda f: (bool((f.metadata or {}).get("edited")), _when(f)))


def _supersede(store: PersonaStore, user_id: UserId, metadata: dict[str, Any], at: datetime,
               by_fact_id: Optional[str], keep: Iterable[str] = ()) -> None:
    kept = set(keep)
    for key in source_keys(metadata or {}):
        if key not in kept:
            store.supersede(user_id, key, at, by_fact_id)


def _superseded(fact: Fact) -> Superseded:
    return Superseded(id=fact.id or "", text=fact.text, stated_at=fact.stated_at)


def _clean(fact: Fact) -> Fact:
    meta = fact.metadata or {}
    if UNRECONCILED not in meta:
        return fact
    return fact.model_copy(update={"metadata": {k: v for k, v in meta.items() if k != UNRECONCILED}})


def _when(fact: Fact) -> datetime:
    when = fact.stated_at or fact.updated_at or fact.created_at
    return _aware(when) if when else _EPOCH


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


class _StoreEmbedder:
    """Lets the dry-run copy embed with the real store's model."""

    def __init__(self, store: PersonaStore) -> None:
        self._store = store

    def embed(self, texts: list[str], input_type: str = "document") -> list[list[float]]:
        return [self._store.embed(t) for t in texts]
