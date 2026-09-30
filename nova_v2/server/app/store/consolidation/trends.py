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

THE ACTION SHAPES
`action` has been written in several shapes over the project's life and rows
from all of them are still in the log. None of that lives here: Action.from_episode
is the one place that knows about it (tools/action.py), and this module asks it
for Actions.

COUNTING INCREMENTALLY
A trend's support is a count over the user's whole history, but the whole
history need not be re-read to know it. Rows are first boiled down to a Tally
per (signal, normalised value) - everything a Candidate is later built from -
and tallies of two spans of the log merge into exactly the tally of both
(merge_tallies). So consolidation keeps the tallies between runs and only
reads the episodes since the last one. The substring merge is applied when
candidates are built, over the tallies, which is why it gives the same answer
as counting everything at once. find_candidates is the full recount, built on
the same two steps.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta
from typing import Any, Iterator

from app.store.consolidation.models import MIN_SUPPORT, Candidate
from app.tools.core.action import Action

# Tool parameters worth counting, as {tool_name: [field, ...]}. A field named
# here becomes the signal "<tool>:<field>".
COUNTED_ACTION_FIELDS: dict[str, list[str]] = {
    "navigation_departure_time": ["destination"],
    "notification_management": ["action"],
}

# Event fields worth counting, as {event_type: [field, ...]} -> "<type>:<field>".
COUNTED_EVENT_FIELDS: dict[str, list[str]] = {
    "notification": ["app"],
    # Content-free voice-note traces (api/notes.py _log_captured): which
    # calendar event the user keeps capturing notes in. Never the note text.
    "note_captured": ["calendar_title"],
}

# Values too generic to be a trend even when they repeat.
IGNORED_VALUES = {"", "unknown", "none", "null", "n/a"}

# How many of the user's own phrasings to keep per candidate, for the phrasing
# pass to see. Enough to show the range, few enough to stay cheap in the prompt.
MAX_EXEMPLARS = 4

# Bump whenever what a Tally holds, or what counts (COUNTED_*, IGNORED_VALUES,
# _norm), changes: stored tallies from an older version are then rebuilt from
# the whole log rather than merged into.
TALLY_VERSION = 1

TallyKey = tuple[str, str]


@dataclass
class Tally:
    """One (signal, normalised value) boiled down from the rows that carry it -
    everything _candidate needs, in row order.

    Each row counts once per time it carries the pair (a turn with two calls to
    the same destination counts twice, as it always has). `exemplars` keeps a
    few more than MAX_EXEMPLARS so that, after the substring merge, the first
    MAX_EXEMPLARS distinct phrasings are still the ones a full recount picks.
    """

    signal: str
    value: str
    support: int = 0
    times: list[str] = dc_field(default_factory=list)       # local wall-clock ISO, row order
    episode_ids: list[str] = dc_field(default_factory=list)
    exemplars: list[str] = dc_field(default_factory=list)
    originals: list[str] = dc_field(default_factory=list)   # raw spellings seen, first-seen order

    def to_json(self) -> dict[str, Any]:
        return {"signal": self.signal, "value": self.value, "support": self.support,
                "times": self.times, "episode_ids": self.episode_ids,
                "exemplars": self.exemplars, "originals": self.originals}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Tally":
        return cls(signal=d["signal"], value=d["value"], support=int(d.get("support", 0)),
                   times=list(d.get("times") or []), episode_ids=list(d.get("episode_ids") or []),
                   exemplars=list(d.get("exemplars") or []), originals=list(d.get("originals") or []))


_KEPT_EXEMPLARS = MAX_EXEMPLARS * 2


def tally_rows(rows: list[dict[str, Any]]) -> dict[TallyKey, Tally]:
    """Boil `rows` (oldest first) down to one Tally per (signal, normalised value)."""
    tallies: dict[TallyKey, Tally] = {}
    for row in rows:
        for signal, value in _signals_of(row):
            key = (signal, _norm(value))
            t = tallies.get(key)
            if t is None:
                t = tallies[key] = Tally(signal, key[1])
            _add_row(t, row)
    return tallies


def _add_row(t: Tally, row: dict[str, Any]) -> None:
    t.support += 1
    when = _when(row)
    if when is not None:
        t.times.append(when.isoformat())
    if row.get("id"):
        t.episode_ids.append(str(row["id"]))
    text = (row.get("event") or {}).get("text")
    if isinstance(text, str) and text and text not in t.exemplars and len(t.exemplars) < _KEPT_EXEMPLARS:
        t.exemplars.append(text)
    for raw in _originals_of(row, t.signal):
        if raw not in t.originals:
            t.originals.append(raw)


def merge_tallies(old: dict[TallyKey, Tally], new: dict[TallyKey, Tally]) -> dict[TallyKey, Tally]:
    """The tallies of an earlier span of the log and the span straight after it,
    as one - the same as tallying both spans' rows together."""
    merged = {k: Tally.from_json(t.to_json()) for k, t in old.items()}
    for key, t in new.items():
        into = merged.get(key)
        if into is None:
            merged[key] = Tally.from_json(t.to_json())
            continue
        into.support += t.support
        into.times.extend(t.times)
        into.episode_ids.extend(t.episode_ids)
        for text in t.exemplars:
            if text not in into.exemplars and len(into.exemplars) < _KEPT_EXEMPLARS:
                into.exemplars.append(text)
        for raw in t.originals:
            if raw not in into.originals:
                into.originals.append(raw)
    return merged


def tallies_to_json(tallies: dict[TallyKey, Tally]) -> list[dict[str, Any]]:
    return [t.to_json() for t in tallies.values()]


def tallies_from_json(items: list[dict[str, Any]] | None) -> dict[TallyKey, Tally]:
    tallies: dict[TallyKey, Tally] = {}
    for d in items or []:
        t = Tally.from_json(d)
        tallies[(t.signal, t.value)] = t
    return tallies


def candidates_from_tallies(
    tallies: dict[TallyKey, Tally], min_support: int = MIN_SUPPORT
) -> list[Candidate]:
    """Every repetition the tallies hold that clears `min_support`, strongest first."""
    groups = _merge_contained({key: [t] for key, t in tallies.items()})
    candidates = [
        _candidate(signal, key, parts)
        for (signal, key), parts in groups.items()
        if sum(p.support for p in parts) >= min_support
    ]
    candidates.sort(key=lambda c: (c.support, c.span_days), reverse=True)
    return candidates


def find_candidates(
    rows: list[dict[str, Any]], min_support: int = MIN_SUPPORT
) -> list[Candidate]:
    """Every repetition in `rows` that clears `min_support`, strongest first."""
    return candidates_from_tallies(tally_rows(rows), min_support)


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
    """This row's Actions as [{tool, params}, ...], for counting.

    Every shape the `action` column has ever been written in is handled by
    Action.from_episode, not here. What is decided here is which of them count:
    Actions the Controller refused are dropped, because NOVA wanting to do
    something it was not authorised to do is not the user doing it, and counting
    it would let a Tool talk itself into a habit its owner never had.
    """
    return [
        {"tool": a.tool, "params": a.input}
        for a in Action.from_episode(row.get("action"))
        if a.ran
    ]


def _norm(value: str) -> str:
    return " ".join(str(value).lower().split())


def _merge_contained(groups: dict[TallyKey, list[Tally]]) -> dict[TallyKey, list[Tally]]:
    """Fold a value into a longer one that contains it, within the same signal.

    "Brooklyn Boy Bagels" and "Brooklyn Boy Bagels, Fyshwick" are one place
    written two ways, and left apart neither might clear MIN_SUPPORT. Substring
    containment is a blunt rule and it is the honest limit of this pass: two
    names for one place that share no substring ("ANU" / "the university") stay
    separate, and no amount of counting will join them.
    """
    merged: dict[TallyKey, list[Tally]] = {}
    for (signal, value), parts in sorted(groups.items(), key=lambda kv: -len(kv[0][1])):
        target = next(
            (k for k in merged if k[0] == signal and value in k[1]),
            None,
        )
        if target is not None:
            merged[target].extend(parts)
        else:
            merged[(signal, value)] = list(parts)
    return merged


# --- candidate assembly ------------------------------------------------------

def _candidate(signal: str, value: str, parts: list[Tally]) -> Candidate:
    times = sorted(datetime.fromisoformat(t) for p in parts for t in p.times)
    span_days = round((times[-1] - times[0]).total_seconds() / 86400, 2) if len(times) > 1 else 0.0

    return Candidate(
        signal=signal,
        value=_display_value(value, parts),
        support=sum(p.support for p in parts),
        span_days=span_days,
        first_seen=times[0].isoformat() if times else None,
        last_seen=times[-1].isoformat() if times else None,
        episode_ids=[e for p in parts for e in p.episode_ids],
        exemplars=_exemplars(parts),
        hours=[t.hour for t in times],
    )


def _originals_of(row: dict[str, Any], signal: str) -> Iterator[str]:
    """Every raw spelling of `signal`'s field this row holds - its tool calls'
    and its event's - for picking the display value later."""
    field_name = signal.split(":", 1)[-1]
    for call in _calls_of(row):
        raw = (call.get("params") or {}).get(field_name)
        if isinstance(raw, str):
            yield raw
    raw = (row.get("event") or {}).get(field_name)
    if isinstance(raw, str):
        yield raw


def _display_value(norm_value: str, parts: list[Tally]) -> str:
    """The recurring value as the user's data actually spells it.

    Groups are keyed on a normalised string, and _merge_contained may have
    folded several spellings together, so pick the longest original that this
    key covers - "Brooklyn Boy Bagels, Fyshwick" over "brooklyn boy bagels".
    """
    covered = [
        raw for p in parts for raw in p.originals
        if norm_value.startswith(_norm(raw))
    ]
    return max(covered, key=len) if covered else norm_value


def _exemplars(parts: list[Tally]) -> list[str]:
    """A few of the user's own phrasings, so the phrasing pass can hear how
    they talk about this rather than only seeing the resolved entity."""
    seen: list[str] = []
    for p in parts:
        for text in p.exemplars:
            if text not in seen:
                seen.append(text)
            if len(seen) >= MAX_EXEMPLARS:
                return seen
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
