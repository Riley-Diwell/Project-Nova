"""
store/profile.py - each user's profile and onboarding answers  (Riley, 5.7)

What onboarding asked, kept per account in the `profiles` table (db/schema.sql
section 8), and what the answers set going:

    get(user_id)                         -> ProfileOut, or None before onboarding
    complete_onboarding(user_id, body)   -> ProfileOut   (POST /me/onboarding)
    update(user_id, patch)               -> ProfileOut   (PATCH /me/profile)
    for_turn(user_id)                    -> ProfileOut or None, never raises

Answers go three places:
  - here, as typed JSON (schemas/profile.OnboardingAnswers);
  - Persona, as stated facts - "Is on campus on Monday and Thursday" - so the
    model knows them and the Knowledge Map shows them. Each is keyed by its
    question: editing an answer rewrites its fact, clearing one deletes it, and
    a fact the user deletes from the map stays deleted until they change that
    answer (tombstone onboarding:<question_id>);
  - the user's starting gains, from the proactivity answer - first time only.
    Changing it later is the Gain screen's job, not a silent reset of dials the
    user may have tuned since.
The estimator reads the answers each turn through for_turn() and
OnboardingAnswers.to_features().

Every function takes the user explicitly, from the verified token (rule 1).
Tests swap the Supabase store for InMemoryProfileStore with set_store().
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Protocol, Union
from uuid import UUID

from app.core.db import get_client
from app.schemas.profile import (
    ONBOARDING_VERSION,
    OnboardingAnswers,
    OnboardingIn,
    ProfileOut,
    ProfilePatch,
)

UserId = Union[UUID, str]

_TABLE = "profiles"

ORIGIN = "onboarding"

# Where the proactivity answer puts every Function tool's learned gain. "A little"
# is today's seed (DEFAULT_GAIN 0.2, R5), so it changes nothing.
PROACTIVE_GAIN = 0.5


class ProfileStore(Protocol):
    def get(self, user_id: UserId) -> Optional[dict[str, Any]]: ...
    def put(self, user_id: UserId, row: dict[str, Any]) -> dict[str, Any]: ...


class SupabaseProfileStore:
    def get(self, user_id: UserId) -> Optional[dict[str, Any]]:
        rows = get_client().table(_TABLE).select("*").eq("user_id", str(user_id)).limit(1).execute().data
        return rows[0] if rows else None

    def put(self, user_id: UserId, row: dict[str, Any]) -> dict[str, Any]:
        # Keyed by the verified user's id, never one from a request body.
        rows = (
            get_client().table(_TABLE)
            .upsert({**row, "user_id": str(user_id)}, on_conflict="user_id")
            .execute().data
        )
        return rows[0]


class InMemoryProfileStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def get(self, user_id: UserId) -> Optional[dict[str, Any]]:
        row = self.rows.get(str(user_id))
        return dict(row) if row else None

    def put(self, user_id: UserId, row: dict[str, Any]) -> dict[str, Any]:
        stored = {**self.rows.get(str(user_id), {}), **row, "user_id": str(user_id)}
        self.rows[str(user_id)] = stored
        return dict(stored)


_store: Optional[ProfileStore] = None


def get_store() -> ProfileStore:
    global _store
    if _store is None:
        _store = SupabaseProfileStore()
    return _store


def set_store(store: Optional[ProfileStore]) -> None:
    global _store
    _store = store


# --- reads ----------------------------------------------------------------------

def get(user_id: UserId) -> Optional[ProfileOut]:
    """This user's profile, or None if they've never saved one."""
    row = get_store().get(user_id)
    return _from_row(row) if row else None


def for_turn(user_id: UserId) -> Optional[ProfileOut]:
    """get(), for the Intent Surface: a turn runs without a profile rather than
    failing because the table is unreachable."""
    try:
        return get(user_id)
    except Exception as e:
        print(f"[profile] read skipped: {e}")
        return None


# --- writes ---------------------------------------------------------------------

def complete_onboarding(user_id: UserId, body: OnboardingIn, now: Optional[datetime] = None) -> ProfileOut:
    """Save the questionnaire and set the user up from it. Safe to repeat (the
    phone retries a failed POST): Persona facts are keyed by question, and
    gains are only seeded the first time."""
    now = now or datetime.now(timezone.utc)
    previous = get(user_id)
    first_time = previous is None or previous.onboarding_completed_at is None

    saved = _put(user_id, {
        "display_name": _clean_name(body.display_name),
        "onboarding_version": ONBOARDING_VERSION,
        "onboarding_completed_at": now.isoformat(),
        "answers": body.answers.model_dump(mode="json"),
        "updated_at": now.isoformat(),
    })
    _seed_persona(user_id, saved.answers, previous.answers if previous else None)
    if first_time:
        _seed_gains(user_id, saved.answers)
    return saved


def update(user_id: UserId, patch: ProfilePatch, now: Optional[datetime] = None) -> ProfileOut:
    """Settings -> Your profile. Only what the patch names changes; the answers
    are validated as a whole after merging (pydantic ValidationError -> 422)."""
    now = now or datetime.now(timezone.utc)
    current = get(user_id) or ProfileOut()
    row: dict[str, Any] = {"updated_at": now.isoformat()}

    if "display_name" in patch.model_fields_set:
        row["display_name"] = _clean_name(patch.display_name)
    answers = current.answers
    if patch.answers is not None:
        merged = {**current.answers.model_dump(mode="json"), **patch.answers}
        answers = OnboardingAnswers.model_validate(merged)
        row["answers"] = answers.model_dump(mode="json")

    saved = _put(user_id, row)
    if patch.answers is not None:
        _seed_persona(user_id, answers, current.answers)
    return saved


def _put(user_id: UserId, row: dict[str, Any]) -> ProfileOut:
    return _from_row(get_store().put(user_id, row))


def _from_row(row: dict[str, Any]) -> ProfileOut:
    return ProfileOut(
        display_name=row.get("display_name"),
        onboarding_version=row.get("onboarding_version") or 0,
        onboarding_completed_at=row.get("onboarding_completed_at"),
        answers=OnboardingAnswers.model_validate(row.get("answers") or {}),
    )


def _clean_name(name: Optional[str]) -> Optional[str]:
    name = (name or "").strip()
    return name or None


# --- Persona: one stated fact per answered question ---------------------------------

_DAY_NAMES = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
              "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}
_TRAVEL = {"transit": "by public transport", "walking": "on foot", "driving": "by car"}
_FOCUS = {
    "urgent_only": "In class or while focusing, only wants to be interrupted for urgent things.",
    "important": "In class or while focusing, wants to be interrupted for important things only.",
    "anything_useful": "Is happy to be interrupted in class or while focusing if it's useful.",
}
_PROACTIVITY = {
    "only_when_asked": "Wants Nova to act only when asked.",
    "a_little": "Wants Nova to act on its own a little.",
    "proactive": "Wants Nova to be proactive.",
}


def statements(answers: OnboardingAnswers) -> dict[str, tuple[str, list[str]]]:
    """{question_id: (fact text, category)} for every answered question.
    Unanswered ones are simply absent. Timetable-in-calendar is an estimator
    input, not something about the user worth a fact."""
    facts: dict[str, tuple[str, list[str]]] = {}
    if answers.campus_days:
        days = [_DAY_NAMES[d] for d in _DAY_NAMES if d in answers.campus_days]
        facts["campus_days"] = (f"Is on campus on {_join(days)}.", ["routines", "schedule"])
    if answers.class_times:
        times = [t for t in ("morning", "afternoon", "evening") if t in answers.class_times]
        facts["class_times"] = (
            f"Usually has classes in the {_join(times, the=True)}.", ["routines", "schedule"])
    if answers.sleep_start or answers.sleep_end:
        start = answers.sleep_start.strftime("%H:%M") if answers.sleep_start else None
        end = answers.sleep_end.strftime("%H:%M") if answers.sleep_end else None
        text = (f"Usually sleeps from {start} to {end}." if start and end
                else f"Usually goes to sleep around {start}." if start
                else f"Usually wakes up around {end}.")
        facts["sleep"] = (text, ["routines", "sleep"])
    if answers.travel_mode:
        facts["travel_mode"] = (
            f"Usually gets to uni {_TRAVEL[answers.travel_mode]}.", ["preferences", "travel"])
    if answers.focus_interruptions:
        facts["focus_interruptions"] = (
            _FOCUS[answers.focus_interruptions], ["preferences", "interruptions"])
    if answers.proactivity:
        facts["proactivity"] = (_PROACTIVITY[answers.proactivity], ["preferences", "assistant"])
    return facts


def _join(items: list[str], the: bool = False) -> str:
    if len(items) == 1:
        return items[0]
    joiner = " and the " if the else " and "
    return ", ".join(items[:-1]) + joiner + items[-1]


def _seed_persona(user_id: UserId, answers: OnboardingAnswers, previous: Optional[OnboardingAnswers]) -> None:
    """Bring this user's onboarding facts in line with their answers. Best-effort:
    the answers are saved either way, and the next edit tries again."""
    from app.store import persona

    try:
        held = {
            f.metadata["question_id"]: f
            for f in persona.all_facts(user_id)
            if (f.metadata or {}).get("origin") == ORIGIN and f.metadata.get("question_id")
        }
        forgotten = persona.forgotten(user_id)
        wanted = statements(answers)
        before = statements(previous) if previous else {}

        for question_id, (text, category) in wanted.items():
            fact = held.get(question_id)
            if fact and fact.text == text:
                continue
            # Deleted from the Knowledge Map, and the answer hasn't changed since:
            # the user already said they don't want this one.
            if (persona.onboarding_key(question_id) in forgotten
                    and before.get(question_id, (None,))[0] == text):
                continue
            persona.upsert(user_id, persona.Fact(
                id=fact.id if fact else None,
                text=text,
                category=category,
                confidence=1.0,
                metadata={"source": "stated", "origin": ORIGIN, "question_id": question_id},
            ))
        for question_id, fact in held.items():
            if question_id not in wanted:  # answer cleared
                persona.delete(user_id, fact.id)
    except Exception as e:
        print(f"[profile] Persona not updated from onboarding: {e}")


# --- gains ------------------------------------------------------------------------

def _seed_gains(user_id: UserId, answers: OnboardingAnswers) -> None:
    """Start every Function tool's dial where the proactivity answer says.
    "Only when I ask" is an override at 0 - a user instruction, so reinforcement
    can't talk it back up; "proactive" raises the learned value instead, so it
    still moves with how the user responds."""
    from app.control.gain.gain_store import GainStore

    if answers.proactivity in (None, "a_little"):
        return
    try:
        store = GainStore().for_user(user_id)
        for gain in store.load_all().values():
            if answers.proactivity == "only_when_asked":
                gain.set_override(0.0)
            else:
                gain.value = max(gain.value, PROACTIVE_GAIN)
            store.save(gain)
    except Exception as e:
        print(f"[profile] starting gains not set: {e}")
