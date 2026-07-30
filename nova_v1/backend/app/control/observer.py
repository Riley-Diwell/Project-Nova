"""control/observer.py - where is the user now, and where are they heading?

WHAT THIS FILE IS
The Observer. It turns an Event plus the User State the phone computed for it
into an **Observation**: the current state, a predicted next state over a bounded
horizon, and how much that prediction is worth.

WHY IT EXISTS
NOVA was described as a control loop and had no observer. A next-state estimator
existed early in the project and was deleted before it reached the live path
(state_estimator/state_estimator.py, still in the tree, marked superseded), and
nothing replaced it. Without a predicted next state there is no error signal, and
without an error signal Controller Gain had nothing to multiply - so it multiplied
the State Estimator's confidence in its own current estimate instead, which
answers "how sure are we about now" and never "how far off are we".

WHY IT IS NOT A SECOND LLM
The deleted estimator was a model call. This one is arithmetic. Three reasons:

  determinism  the same Episode replays to the same Observation, so a misfire can
               be reproduced from the log rather than from an impression of what
               the model was probably thinking (stories 18, 25).
  latency      one model call per turn and one latency budget (story 30). An
               observer that called out would double both.
  auditability ADR-0004 decided that counting decides what is true and the model
               only words it, because a belief whose confidence came from an
               impression cannot be audited. An *action* whose justification came
               from an impression cannot be audited either.

Pure function: no clock read, no network, no store, no model. `now` comes from
the Event's own timestamp and the offset riding on the User State, which is also
what makes the Observation replayable.

WHAT IT READS
Calendar entries and their `minutes_until_start`, `calendar_ctx`, location,
activity, the local hour, and the Trends Consolidation has already counted into
Persona. It does not read Memory: that is a network call, and short-term episodes
are the Intent Surface's context rather than evidence about where the user is
heading.

WHAT IT DOES NOT DO
It does not decide anything. Which Tools may act on this Observation is the
Controller's job (control/controller.py), and how far off a given Tool is, is the
Tool's own (BaseTool.error).
"""
from __future__ import annotations

from datetime import timedelta
from enum import Enum
from typing import Any, Iterable, Optional, Sequence

from pydantic import BaseModel, Field

try:                                                   # app.control.observer
    from ..gain.config import clamp
    from ..schemas.user_state import UserState
    from ..tools.notification_batcher import Mode
except ImportError:  # pragma: no cover                 # control.observer
    from gain.config import clamp
    from schemas.user_state import UserState
    from tools.notification_batcher import Mode

# How far ahead the Observer will predict, in minutes. One hour, matching the
# scope of the estimator this replaces. Beyond it the prediction stops being
# about the situation and starts being about the diary, which is a question the
# user can just ask.
HORIZON_MINUTES = 60

# Hours on the user's own clock during which an interruption is a different kind
# of event. Named and tunable rather than inline, and applied to the local hour
# so it means the same thing in every timezone.
QUIET_HOURS = frozenset({23, 0, 1, 2, 3, 4, 5})

# Activities during which the user's hands and eyes are committed elsewhere.
HANDS_BUSY_ACTIVITIES = frozenset({"in_vehicle", "driving", "on_bicycle",
                                   "cycling", "running", "walking_fast"})

# Android's NotificationManager filters. "all" is the permissive one; the rest
# are the user having asked for less.
RESTRICTIVE_FILTERS = frozenset({"priority", "alarms", "none"})


class PredictedState(str, Enum):
    """Where the user is heading over the horizon.

    Deliberately coarse. A richer state space is a later swap - the Controller
    only asks each Tool for a number, so nothing outside this module and the
    error terms knows how many states there are.
    """

    UNKNOWN = "unknown"          # nothing to go on; the closed loop sits out
    FREE = "free"                # no commitment in the horizon
    IN_EVENT = "in_event"        # in something, or about to be, with nowhere to go
    TRAVELLING = "travelling"    # committed somewhere they are not


class Trend(BaseModel):
    """A repetition Consolidation counted, as the Observer needs it.

    Not a Persona Fact and not a Candidate: just the counted identity and how
    much evidence is behind it. The Observer uses these to corroborate a
    prediction - a commitment at a place this user habitually goes to is a
    better-supported prediction than one at a place they have never been.
    """

    model_config = {"frozen": True}

    signal: str
    value: str
    support: int = 0


class Commitment(BaseModel):
    """One calendar entry the user is actually committed to, inside the horizon.

    `minutes_until_start` is the phone's own arithmetic against its own clock and
    is carried through rather than recomputed - the backend has no second clock
    that could be trusted more, and recomputing it from `start_millis` is exactly
    the timezone trap the Intent Surface's prompt used to spend a paragraph on.
    """

    model_config = {"frozen": True}

    title: str
    location: Optional[str] = None
    minutes_until_start: int
    minutes_until_end: int
    availability: str = "busy"
    self_status: str = "none"


class Observation(BaseModel):
    """What the Observer saw, and what it expects next.

    Backend-side only. `UserState` on the wire is unchanged, so this crosses no
    frozen seam and the Android client needs no coordinated release.
    """

    model_config = {"frozen": True}

    # --- the current state ---------------------------------------------------
    activity: Optional[str] = None
    location_ctx: Optional[str] = None
    calendar_ctx: Optional[str] = None
    local_hour: Optional[int] = None
    in_event: bool = False

    # The State Estimator's confidence in its estimate of *now*. Carried for the
    # log, and deliberately NOT what the control law multiplies - multiplying
    # willingness by certainty is the bug this overhaul exists to remove.
    state_confidence: float = 0.0

    # How available the user is to be interrupted, 0 to 1. Read from dnd,
    # ringer_mode, interruption_filter, calendar_ctx, call state and activity.
    interruptibility: float = 1.0

    # Derived, not held. The notification batcher used to keep its own copy of
    # this, which was a second source of truth for what calendar_ctx and dnd
    # already say.
    mode: Mode = Mode.DEFAULT

    # --- the horizon ---------------------------------------------------------
    horizon_minutes: int = HORIZON_MINUTES
    commitments: list[Commitment] = Field(default_factory=list)
    next_commitment: Optional[Commitment] = None

    # --- the prediction ------------------------------------------------------
    predicted: PredictedState = PredictedState.UNKNOWN

    # How much the prediction is worth, 0 to 1. Zero where no prediction was
    # possible, and the control law multiplies by it, so an Observation with
    # nothing to go on contributes no authority and NOVA falls back to purely
    # reactive behaviour instead of guessing.
    prediction_confidence: float = 0.0

    # --- what Consolidation has counted --------------------------------------
    habitual_places: list[str] = Field(default_factory=list)


# Evidence weights for the prediction confidence. They sum to 1.0, so a fully
# corroborated prediction is worth 1.0 and a bare one is worth what it had.
# This is the tuning surface for "how much do we trust a guess", and it is one
# dict rather than a number buried in a branch.
PREDICTION_EVIDENCE_WEIGHTS: dict[str, float] = {
    "calendar": 0.4,        # entries or a calendar_ctx to read them against
    "activity": 0.2,        # what the user is physically doing
    "location": 0.2,        # where they are
    "corroboration": 0.2,   # a counted Trend agrees with the prediction
}


def observe(
    event: Any,
    user_state: UserState,
    trends: Sequence[Trend] = (),
) -> Observation:
    """The Observation for one pipeline pass.

    `trends` are the Trends Consolidation has counted into Persona, if any were
    retrieved for this Event. Passing none is normal and costs only prediction
    confidence - Persona needs Supabase and an embedding model, and a turn has to
    run without either.
    """
    local_hour = _local_hour(event, user_state)
    commitments = _commitments(user_state)
    in_event = _in_event(user_state)
    habitual = _habitual_places(trends)

    predicted = _predict(commitments, in_event, _has_calendar_evidence(user_state))
    confidence = _prediction_confidence(
        predicted, user_state, commitments, habitual
    )

    return Observation(
        activity=_activity(user_state),
        location_ctx=user_state.location_ctx,
        calendar_ctx=user_state.calendar_ctx,
        local_hour=local_hour,
        in_event=in_event,
        state_confidence=_unit(user_state.confidence),
        interruptibility=_interruptibility(user_state, in_event),
        mode=_mode(user_state, in_event, local_hour),
        horizon_minutes=HORIZON_MINUTES,
        commitments=commitments,
        next_commitment=commitments[0] if commitments else None,
        predicted=predicted,
        prediction_confidence=confidence,
        habitual_places=habitual,
    )


def trends_from_facts(facts: Iterable[Any]) -> list[Trend]:
    """The Trends among some Persona facts.

    Only `derived` facts qualify: a Trend is a counted repetition, and a fact the
    user simply stated was never counted. The evidence block Consolidation writes
    (models.Candidate.evidence) is what carries the counted identity through, so a
    fact without one is a fact this cannot use - older rows predate it.

    Takes facts as either Fact models or the plain dicts the Intent Surface's
    payload holds, because both callers exist.
    """
    trends: list[Trend] = []
    for fact in facts:
        metadata = _metadata_of(fact)
        if metadata.get("source") != "derived":
            continue
        signal, value = metadata.get("signal"), metadata.get("value")
        if not isinstance(signal, str) or not isinstance(value, str) or not value:
            continue
        support = metadata.get("support")
        trends.append(Trend(
            signal=signal,
            value=value,
            support=support if isinstance(support, int) else 0,
        ))
    return trends


# --- current state -----------------------------------------------------------

def _local_hour(event: Any, user_state: UserState) -> Optional[int]:
    """The hour on the user's own clock.

    The Event timestamp is UTC and the offset rides along on the User State, so
    the two together are the only reading of "now" that exists here. No clock is
    read - that is what makes an Observation replayable months later.
    """
    timestamp = getattr(event, "timestamp", None)
    if timestamp is None:
        return None
    offset = user_state.utc_offset_minutes
    return (timestamp + timedelta(minutes=offset)).hour


def _activity(user_state: UserState) -> Optional[str]:
    """What the user is doing, from whichever signal has it.

    `activity` is ActivityRecognition's label and `motion` is the accelerometer's;
    either will do, and the phone does not always have both.
    """
    return user_state.activity or user_state.motion


def _in_event(user_state: UserState) -> bool:
    """Is the user in something right now?

    Two sources agree or they do not: `calendar_ctx` is the phone's own summary,
    and a current entry that has started but not finished is the detail behind
    it. Either is enough, because the summary survives when the entry list is
    permission-gated away.
    """
    if user_state.calendar_ctx == "in_event":
        return True
    return any(
        _is_committed(e) and e.minutes_until_start <= 0
        for e in user_state.current_events or []
    )


def _interruptibility(user_state: UserState, in_event: bool) -> float:
    """How available the user is to be interrupted, 0 to 1.

    A budget, not a verdict: the notification error term measures pressure
    against it rather than reading it as a yes or no, so a genuinely urgent
    notification still gets through a lecture (story 12) while a routine one
    waits (story 11).

    Penalties are additive and every one of them is the user having asked for
    less noise, or the situation making noise expensive. The screen bonus is the
    other direction: someone already looking at their phone is cheap to
    interrupt, which is the honest reading even though it is the opposite of what
    the product wants long-term.
    """
    budget = 1.0

    if user_state.dnd:
        budget -= 0.5
    if user_state.interruption_filter in RESTRICTIVE_FILTERS:
        budget -= 0.3

    ringer = user_state.ringer_mode
    if ringer == "silent":
        budget -= 0.2
    elif ringer == "vibrate":
        budget -= 0.1

    if in_event:
        budget -= 0.3
    if _activity(user_state) in HANDS_BUSY_ACTIVITIES:
        budget -= 0.3
    if user_state.call_state in ("offhook", "ringing"):
        budget -= 0.5

    if user_state.screen and user_state.foreground_app:
        budget += 0.1

    return _unit(budget)


def _mode(user_state: UserState, in_event: bool, local_hour: Optional[int]) -> Mode:
    """Which of the batcher's delivery modes this situation is.

    Derived here so the batcher stops holding a second copy of something
    `calendar_ctx` and `dnd` already carry. Most restrictive wins.
    """
    if local_hour is not None and local_hour in QUIET_HOURS:
        return Mode.SLEEP
    if in_event:
        return Mode.LECTURE
    if (user_state.dnd
            or user_state.interruption_filter in RESTRICTIVE_FILTERS):
        return Mode.FOCUS
    return Mode.DEFAULT


# --- the horizon -------------------------------------------------------------

def _commitments(user_state: UserState) -> list[Commitment]:
    """Everything inside the horizon the user is actually committed to, soonest
    first.

    Current events are included so an entry already under way is visible, but
    only future starts count as something to get to. An entry the user declined,
    or one marked free, commits them to nothing - story 2's silence depends on
    that, or every optional invitation in the diary would read as divergence.
    """
    entries = [
        *(user_state.current_events or []),
        *(user_state.upcoming_events or []),
    ]
    inside = [
        _commitment(e) for e in entries
        if _is_committed(e) and 0 <= e.minutes_until_start <= HORIZON_MINUTES
    ]
    inside.sort(key=lambda c: c.minutes_until_start)
    return inside


def _is_committed(entry: Any) -> bool:
    """Busy, and not turned down."""
    return (getattr(entry, "availability", "busy") == "busy"
            and getattr(entry, "self_status", "none") != "declined")


def _commitment(entry: Any) -> Commitment:
    duration = max(
        0,
        round((getattr(entry, "end_millis", 0) - getattr(entry, "start_millis", 0)) / 60_000),
    )
    return Commitment(
        title=getattr(entry, "title", ""),
        location=getattr(entry, "location", None) or None,
        minutes_until_start=entry.minutes_until_start,
        minutes_until_end=entry.minutes_until_start + duration,
        availability=getattr(entry, "availability", "busy"),
        self_status=getattr(entry, "self_status", "none"),
    )


# --- the prediction ----------------------------------------------------------

def _predict(
    commitments: list[Commitment], in_event: bool, has_calendar: bool
) -> PredictedState:
    """Where the next hour goes.

    The order matters. Somewhere to be beats being somewhere, because a place to
    get to is the only one of these with a deadline attached - and a deadline is
    what an error term can measure divergence against.
    """
    next_up = commitments[0] if commitments else None

    if next_up is not None:
        return PredictedState.TRAVELLING if next_up.location else PredictedState.IN_EVENT
    if in_event:
        return PredictedState.IN_EVENT
    if has_calendar:
        return PredictedState.FREE
    return PredictedState.UNKNOWN


def _has_calendar_evidence(user_state: UserState) -> bool:
    """Did we actually see the calendar, or is it just absent?

    The difference between "nothing on for an hour" and "no calendar permission",
    and therefore the difference between predicting FREE and admitting UNKNOWN.
    """
    return bool(
        user_state.calendar_ctx
        or user_state.current_events
        or user_state.upcoming_events
    )


def _prediction_confidence(
    predicted: PredictedState,
    user_state: UserState,
    commitments: list[Commitment],
    habitual_places: list[str],
) -> float:
    """How much the prediction is worth, 0 to 1.

    Zero where there was no prediction: the closed loop then contributes nothing
    that turn and NOVA falls back to reactive behaviour, which is the honest
    answer and is why the control law multiplies by this rather than adding it.

    Otherwise it is the sum of the evidence that was actually present. Nothing
    here is a probability - it is a statement about how much the Observer had to
    go on, which is the thing the loop should de-rate control effort against.
    """
    if predicted is PredictedState.UNKNOWN:
        return 0.0

    weights = PREDICTION_EVIDENCE_WEIGHTS
    score = 0.0
    if _has_calendar_evidence(user_state):
        score += weights["calendar"]
    if _activity(user_state):
        score += weights["activity"]
    if user_state.location_ctx:
        score += weights["location"]
    if _corroborated(commitments, habitual_places):
        score += weights["corroboration"]

    return _unit(score)


def _corroborated(commitments: list[Commitment], habitual_places: list[str]) -> bool:
    """Does a counted habit agree with where we think the user is going?

    Substring either way, because a Trend's value is spelled however the tool
    call resolved it ("Brooklyn Boy Bagels, Fyshwick") and a calendar location is
    spelled however the user typed it. The same blunt rule Consolidation uses to
    merge two spellings of one place, and the same honest limit: two names for
    one place sharing no substring stay apart.
    """
    for commitment in commitments:
        where = (commitment.location or "").lower()
        if not where:
            continue
        for place in habitual_places:
            known = place.lower()
            if known and (known in where or where in known):
                return True
    return False


def _habitual_places(trends: Sequence[Trend]) -> list[str]:
    """Places this user repeatedly goes, from the counted destination Trends.

    Consolidation names its signals "<tool>:<field>", so the destinations are the
    ones whose field is `destination`. Reading the suffix rather than the whole
    signal keeps this working if another tool grows a destination.
    """
    seen: list[str] = []
    for trend in trends:
        if trend.signal.endswith(":destination") and trend.value not in seen:
            seen.append(trend.value)
    return seen


# --- helpers -----------------------------------------------------------------

def _metadata_of(fact: Any) -> dict[str, Any]:
    """A fact's metadata, whether it arrived as a model or as a plain dict."""
    metadata = fact.get("metadata") if isinstance(fact, dict) else getattr(fact, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def _unit(value: Any) -> float:
    """Clamp to [0, 1]. Every confidence and budget in this module is one.

    Delegates to gain.config.clamp so there is one clamp in the codebase, and adds
    the coercion that one cannot make an assumption about: `confidence` arrives from
    the wire, and an older client or a hand-rolled request can put a string or a
    null there. Reading that as 0.0 is the safe direction - a state we cannot
    measure contributes no authority.
    """
    try:
        return clamp(float(value))
    except (TypeError, ValueError):
        return 0.0
