"""Finding repetitions in the episodic log, by counting.

No model runs here. Everything this file produces is arithmetic over rows, so a
Candidate can be recomputed exactly and checked by hand - which is the point of
splitting it from the phrasing pass in __init__.py.

WHAT GETS COUNTED
Two families of signal, both declared explicitly below rather than discovered.
Counting every string field in every row would find plenty of "trends" that are
artefacts of the schema (every voice event has type "voice"), so the fields
that carry behaviour are named:

  - action signals - a resolved tool parameter, e.g. the `destination` the
    NavigationTool was actually called with. This is the important family: the
    user says "the bagel place", "take me to the bagel shop", "directions to
    Brooklyn Boy Bagels" - five phrasings, one entity, and the entity exists
    only in the tool call. Counting the words they said would find nothing.
  - event signals - a field of the triggering event itself, e.g. which app a
    notification came from. Available without any tool having fired.

TWO ACTION SHAPES
`action` is written in two different shapes and both are read here:
  - {"tool": ..., "params": {...}}            - one call (scripts/seed_memory.py)
  - {"actions": [{"tool":..., "input":...}]}  - many (main.py's _close_episode)
The seeded fixture predates the live writer, and databases seeded with it are
still the demo data, so normalising is cheaper than a migration.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterator

from .models import MIN_SUPPORT, Candidate

# Tool parameters worth counting, as {tool_name: [field, ...]}. A field named
# here becomes the signal "<tool>:<field>".
COUNTED_ACTION_FIELDS: dict[str, list[str]] = {
    "navigation_departure_time": ["destination"],
    "notification_management": ["decision", "action"],
}

# Event fields worth counting, as {event_type: [field, ...]} -> "<type>:<field>".
COUNTED_EVENT_FIELDS: dict[str, list[str]] = {
    "notification": ["app"],
}

# Values too generic to be a trend even when they repeat.
IGNORED_VALUES = {"", "unknown", "none", "null", "n/a"}

# How many of the user's own phrasings to keep per candidate, for the phrasing
# pass to see. Enough to show the range, few enough to stay cheap in the prompt.
MAX_EXEMPLARS = 4


def find_candidates(
    rows: list[dict[str, Any]], min_support: int = MIN_SUPPORT
) -> list[Candidate]:
    """Every repetition in `rows` that clears `min_support`, strongest first."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for signal, value in _signals_of(row):
            groups[(signal, _norm(value))].append(row)

    groups = _merge_contained(groups)

    candidates = [
        _candidate(signal, key, rows_in)
        for (signal, key), rows_in in groups.items()
        if len(rows_in) >= min_support
    ]
    candidates.sort(key=lambda c: (c.support, c.span_days), reverse=True)
    return candidates


# --- signal extraction -------------------------------------------------------

def _signals_of(row: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """Every (signal, value) pair this row contributes."""
    for call in _calls_of(row):
        tool = call.get("tool")
        params = call.get("params") or {}
        for field in COUNTED_ACTION_FIELDS.get(tool, []):
            value = params.get(field)
            if isinstance(value, str) and _norm(value) not in IGNORED_VALUES:
                yield f"{tool}:{field}", value

    event = row.get("event") or {}
    event_type = row.get("event_type") or event.get("type") or ""
    for field in COUNTED_EVENT_FIELDS.get(event_type, []):
        value = event.get(field)
        if isinstance(value, str) and _norm(value) not in IGNORED_VALUES:
            yield f"{event_type}:{field}", value


def _calls_of(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise both `action` shapes into [{tool, params}, ...].

    Suppressed Actions are dropped: the model wanting to do something that gain
    refused is not the user doing it, and counting it would let a tool talk
    itself into a habit the user never had.
    """
    action = row.get("action")
    if not isinstance(action, dict):
        return []

    # "calls" was this list's name before Actions became one concept; episodes
    # written under it are still in the log, so both keys are read.
    entries = action.get("actions")
    if not isinstance(entries, list):
        entries = action.get("calls")
    if isinstance(entries, list):                      # main.py
        return [
            {"tool": c.get("tool"), "params": c.get("input") or {}}
            for c in entries
            if isinstance(c, dict) and c.get("ran", True)
        ]

    if action.get("tool"):                             # seed_memory.py
        return [{"tool": action["tool"], "params": action.get("params") or {}}]

    return []


def _norm(value: str) -> str:
    return " ".join(str(value).lower().split())


def _merge_contained(
    groups: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Fold a value into a longer one that contains it, within the same signal.

    "Brooklyn Boy Bagels" and "Brooklyn Boy Bagels, Fyshwick" are one place
    written two ways, and left apart neither might clear MIN_SUPPORT. Substring
    containment is a blunt rule and it is the honest limit of this pass: two
    names for one place that share no substring ("ANU" / "the university") stay
    separate, and no amount of counting will join them.
    """
    merged: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for (signal, value), rows in sorted(groups.items(), key=lambda kv: -len(kv[0][1])):
        target = next(
            (k for k in merged if k[0] == signal and value in k[1]),
            None,
        )
        if target is not None:
            merged[target].extend(rows)
        else:
            merged[(signal, value)] = list(rows)
    return merged


# --- candidate assembly ------------------------------------------------------

def _candidate(signal: str, value: str, rows: list[dict[str, Any]]) -> Candidate:
    times = sorted(t for t in (_when(r) for r in rows) if t is not None)
    span_days = round((times[-1] - times[0]).total_seconds() / 86400, 2) if len(times) > 1 else 0.0

    return Candidate(
        signal=signal,
        value=_display_value(value, rows, signal),
        support=len(rows),
        span_days=span_days,
        first_seen=times[0].isoformat() if times else None,
        last_seen=times[-1].isoformat() if times else None,
        episode_ids=[str(r["id"]) for r in rows if r.get("id")],
        exemplars=_exemplars(rows),
        hours=[t.hour for t in times],
    )


def _display_value(norm_value: str, rows: list[dict[str, Any]], signal: str) -> str:
    """The recurring value as the user's data actually spells it.

    Groups are keyed on a normalised string, and _merge_contained may have
    folded several spellings together, so pick the longest original that this
    key covers - "Brooklyn Boy Bagels, Fyshwick" over "brooklyn boy bagels".
    """
    field = signal.split(":", 1)[-1]
    originals: list[str] = []
    for row in rows:
        for call in _calls_of(row):
            originals.append((call.get("params") or {}).get(field))
        originals.append((row.get("event") or {}).get(field))

    covered = [
        raw for raw in originals
        if isinstance(raw, str) and norm_value.startswith(_norm(raw))
    ]
    return max(covered, key=len) if covered else norm_value


def _exemplars(rows: list[dict[str, Any]]) -> list[str]:
    """A few of the user's own phrasings, so the phrasing pass can hear how
    they talk about this rather than only seeing the resolved entity."""
    seen: list[str] = []
    for row in rows:
        text = (row.get("event") or {}).get("text")
        if isinstance(text, str) and text and text not in seen:
            seen.append(text)
        if len(seen) >= MAX_EXEMPLARS:
            break
    return seen


def _when(row: dict[str, Any]) -> datetime | None:
    """When this episode happened, on the user's own clock.

    created_at is UTC; user_state.utc_offset_minutes is how far the phone was
    from it at the time. The difference decides whether a habit reads as
    "morning" - the same trap the Intent Surface's prompt warns about - so the
    offset is applied here rather than trusting a stored wall-clock string.
    Reading it per-row matters: a habit formed at home and re-observed abroad
    should still be counted against the clock the user was living on.
    """
    when = _parse(row.get("created_at"))
    if when is None:
        return None

    state = row.get("user_state") or {}
    offset = state.get("utc_offset_minutes")
    return when + timedelta(minutes=offset) if isinstance(offset, int) else when


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None
