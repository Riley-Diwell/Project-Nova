"""Where automatic consolidation is up to, per user - and whether it's due.

One row per user (consolidation_state, db/schema.sql 3c):
  - the watermark: the last episode read (`last_episode_at`, plus the ids read
    at exactly that timestamp, since several episodes can share one), so each
    run reads only what arrived since;
  - the trend tallies (trends.Tally) the counting pass keeps between runs, and
    the TALLY_VERSION they were counted under;
  - a lease, so two runs for the same user never overlap (a voice turn's
    trigger and the phone's daily job, say);
  - when the last run finished, and what it did.

The tallies and the watermark are written together, in one row update, so a
run that dies halfway can't leave episodes counted but not marked as read (or
the reverse) - the next run picks up exactly where the last commit left off.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol

UserId = Any

# Due once this many new (settled) episodes have arrived...
DUE_AFTER_EPISODES = 20
# ...or a day has passed with at least one...
DUE_AFTER = timedelta(hours=24)
# ...but never twice within this.
DEBOUNCE = timedelta(minutes=15)
# Episodes younger than this aren't read yet: their `action` (which the trend
# pass counts) is filled in when the turn closes, after the row is written.
SETTLE = timedelta(minutes=10)
# How long a run may hold its lease before another can take over.
LEASE = timedelta(minutes=5)
# How long a "due?" answer is reused in this process - it's asked on every
# /event, and the answer barely moves between turns.
DUE_MEMO = timedelta(minutes=5)

STATE_TABLE = "consolidation_state"
CLAIM_FN = "claim_consolidation"


@dataclass
class ConsolidationState:
    last_run_at: Optional[datetime] = None
    last_episode_at: Optional[str] = None
    last_episode_ids: list[str] = field(default_factory=list)
    tally_version: int = 0
    tallies: list[dict[str, Any]] = field(default_factory=list)
    lease_holder: Optional[str] = None
    lease_until: Optional[datetime] = None

    def running(self, now: datetime) -> bool:
        return self.lease_until is not None and self.lease_until > now


@dataclass
class Due:
    due: bool
    running: bool = False
    last_run_at: Optional[datetime] = None

    @property
    def status(self) -> str:
        return "running" if self.running else "due" if self.due else "idle"


class StateStore(Protocol):
    def get(self, user_id: UserId) -> Optional[ConsolidationState]: ...
    def claim(self, user_id: UserId, holder: str, ttl: timedelta) -> bool: ...
    def commit(self, user_id: UserId, holder: str, *, tallies: list[dict[str, Any]], tally_version: int,
               last_episode_at: Optional[str], last_episode_ids: list[str]) -> bool: ...
    def finish(self, user_id: UserId, holder: str, result: dict[str, Any]) -> None: ...


def is_due(state: Optional[ConsolidationState], new_episodes: int, now: datetime) -> Due:
    """The rule, on its own so it can be read and tested at a glance."""
    state = state or ConsolidationState()
    if state.running(now):
        return Due(False, running=True, last_run_at=state.last_run_at)
    last = state.last_run_at
    if last is not None and now - last < DEBOUNCE:
        return Due(False, last_run_at=last)
    if last is None:
        return Due(True)
    if new_episodes >= DUE_AFTER_EPISODES:
        return Due(True, last_run_at=last)
    return Due(new_episodes > 0 and now - last >= DUE_AFTER, last_run_at=last)


# --- stores ---------------------------------------------------------------------------

class InMemoryStateStore:
    def __init__(self) -> None:
        self._rows: dict[str, ConsolidationState] = {}
        self._lock = threading.Lock()

    def get(self, user_id: UserId) -> Optional[ConsolidationState]:
        row = self._rows.get(str(user_id))
        return None if row is None else ConsolidationState(**{**row.__dict__, "tallies": list(row.tallies),
                                                               "last_episode_ids": list(row.last_episode_ids)})

    def claim(self, user_id: UserId, holder: str, ttl: timedelta) -> bool:
        now = datetime.now(timezone.utc)
        with self._lock:
            row = self._rows.setdefault(str(user_id), ConsolidationState())
            if row.running(now) and row.lease_holder != holder:
                return False
            row.lease_holder, row.lease_until = holder, now + ttl
            return True

    def commit(self, user_id: UserId, holder: str, *, tallies, tally_version, last_episode_at, last_episode_ids) -> bool:
        with self._lock:
            row = self._rows.get(str(user_id))
            if row is None or row.lease_holder != holder:
                return False
            row.tallies, row.tally_version = list(tallies), tally_version
            row.last_episode_at, row.last_episode_ids = last_episode_at, list(last_episode_ids)
            return True

    def finish(self, user_id: UserId, holder: str, result: dict[str, Any]) -> None:
        with self._lock:
            row = self._rows.get(str(user_id))
            if row is not None and row.lease_holder == holder:
                row.last_run_at = datetime.now(timezone.utc)
                row.lease_holder, row.lease_until = None, None


class SupabaseStateStore:
    def __init__(self, client) -> None:
        self._db = client

    def get(self, user_id: UserId) -> Optional[ConsolidationState]:
        res = self._db.table(STATE_TABLE).select("*").eq("user_id", str(user_id)).execute()
        if not res.data:
            return None
        r = res.data[0]
        return ConsolidationState(
            last_run_at=_time(r.get("last_run_at")),
            last_episode_at=r.get("last_episode_at"),
            last_episode_ids=[str(i) for i in (r.get("last_episode_ids") or [])],
            tally_version=int(r.get("tally_version") or 0),
            tallies=list(r.get("tallies") or []),
            lease_holder=r.get("lease_holder"),
            lease_until=_time(r.get("lease_until")),
        )

    def claim(self, user_id: UserId, holder: str, ttl: timedelta) -> bool:
        res = self._db.rpc(CLAIM_FN, {"p_user": str(user_id), "p_holder": holder,
                                      "p_ttl_ms": int(ttl.total_seconds() * 1000)}).execute()
        return bool(res.data)

    def commit(self, user_id: UserId, holder: str, *, tallies, tally_version, last_episode_at, last_episode_ids) -> bool:
        # One row, one update: the tallies and the watermark land together or not at all.
        res = (
            self._db.table(STATE_TABLE).update({
                "tallies": tallies, "tally_version": tally_version,
                "last_episode_at": last_episode_at, "last_episode_ids": last_episode_ids,
            })
            .eq("user_id", str(user_id)).eq("lease_holder", holder)
            .execute()
        )
        return bool(res.data)

    def finish(self, user_id: UserId, holder: str, result: dict[str, Any]) -> None:
        (self._db.table(STATE_TABLE).update({
            "last_run_at": datetime.now(timezone.utc).isoformat(), "last_result": result,
            "lease_holder": None, "lease_until": None,
        }).eq("user_id", str(user_id)).eq("lease_holder", holder).execute())


def _time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --- which store, and the memoised "due?" -----------------------------------------------

_store: Optional[StateStore] = None
_store_off = False
_memo: dict[str, tuple[float, Due]] = {}
_memo_lock = threading.Lock()


def get_state_store() -> Optional[StateStore]:
    """The consolidation_state table, or None where it isn't available (tests
    without one set, or production before db/schema.sql 3c)."""
    global _store
    if _store_off:
        return None
    if _store is None:
        from app.store import persona
        from app.store.persona import SupabasePersonaStore

        if isinstance(persona.get_store(), SupabasePersonaStore):
            from app.core.db import get_client

            _store = SupabaseStateStore(get_client())
    return _store


def set_state_store(store: Optional[StateStore]) -> None:
    global _store, _store_off
    _store, _store_off = store, False
    forget_due()


def state_failed(e: Exception) -> None:
    """A missing table switches incremental consolidation off for this process
    (it falls back to a full run once a day); anything else is just logged."""
    global _store_off
    if STATE_TABLE in str(e) or CLAIM_FN in str(e):
        if not _store_off:
            print("[consolidation] WARNING: consolidation_state missing - incremental runs off "
                  "until db/schema.sql section 3c is run and the server restarted.")
        _store_off = True
    else:
        print(f"[consolidation] state unavailable: {e}")


def forget_due(user_id: Optional[UserId] = None) -> None:
    with _memo_lock:
        if user_id is None:
            _memo.clear()
        else:
            _memo.pop(str(user_id), None)


def remember_due(user_id: UserId, due: Due) -> None:
    with _memo_lock:
        _memo[str(user_id)] = (time.monotonic(), due)


def memoised_due(user_id: UserId) -> Optional[Due]:
    with _memo_lock:
        hit = _memo.get(str(user_id))
    if hit is None or time.monotonic() - hit[0] > DUE_MEMO.total_seconds():
        return None
    return hit[1]
