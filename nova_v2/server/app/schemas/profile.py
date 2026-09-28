"""
schemas/profile.py - onboarding answers and the profile  (Riley, 5.7; features agreed with Jay)

WHAT THIS FILE IS
The contract for what onboarding asks and what the
server keeps: the answers as typed, versioned JSON, plus the one function that
turns them into estimator inputs for 5.2.

    OnboardingAnswers         every question, each optional (skippable)
    OnboardingAnswers.to_features(local_time) -> {name: float}
    ProfileOut                what GET /me and the /me/* writes return
    OnboardingIn, ProfilePatch  the request bodies

Every answer can be skipped. A skipped answer (None, or an empty list) gives the
feature today's default, so a user who skips everything gets exactly the
behaviour NOVA had before onboarding existed.

FEATURE NAMES ARE A CONTRACT
Jay's estimator reads these by name, so they are stable: add new ones, never
rename or re-scale an existing one, and bump OnboardingAnswers.version when a
question's meaning changes.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Day = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ClassTime = Literal["morning", "afternoon", "evening"]
TravelMode = Literal["transit", "walking", "driving"]

# The newest questionnaire. The phone re-shows onboarding to anyone whose
# profile has an older onboarding_version, so a required question added later
# is asked once - and only that one needs asking.
ONBOARDING_VERSION = 1

# datetime.weekday() order.
_DAYS: tuple[Day, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# What "morning" etc. mean on the user's own clock, as [start, end) hours.
CLASS_TIME_HOURS: dict[str, tuple[int, int]] = {
    "morning": (8, 12),
    "afternoon": (12, 17),
    "evening": (17, 21),
}

# The Observer's QUIET_HOURS before onboarding existed: 23:00 up to 06:00.
DEFAULT_SLEEP_START = time(23, 0)
DEFAULT_SLEEP_END = time(6, 0)

_CALENDAR_RELIABILITY = {"yes": 1.0, "partly": 0.5, "no": 0.0}
_FOCUS_TOLERANCE = {"urgent_only": 0.0, "important": 0.5, "anything_useful": 1.0}


class OnboardingAnswers(BaseModel):
    """Everything onboarding asks. All optional; see the module docstring."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    campus_days: list[Day] = Field(default_factory=list, max_length=7)
    class_times: list[ClassTime] = Field(default_factory=list, max_length=3)
    timetable_in_calendar: Optional[Literal["yes", "partly", "no"]] = None
    sleep_start: Optional[time] = None
    sleep_end: Optional[time] = None
    travel_mode: Optional[TravelMode] = None
    focus_interruptions: Optional[Literal["urgent_only", "important", "anything_useful"]] = None
    proactivity: Optional[Literal["only_when_asked", "a_little", "proactive"]] = None

    def to_features(self, local_time: datetime) -> dict[str, float]:
        """The estimator inputs for one moment on the user's own clock.

        campus_today        1 if today is one of their campus days (0 if unknown)
        class_time_now      1 if now falls in a class time they gave (0 if unknown)
        calendar_reliability  1 / 0.5 / 0 for timetable in calendar yes/partly/no;
                            1 if unknown (trust the calendar, as before)
        in_sleep_window     1 inside their sleep window (default 23:00-06:00)
        sleep_start_hour, sleep_end_hour  the window itself, as fractional hours
        travel_transit, travel_walking, travel_driving  one-hot; all 0 if unknown
        focus_tolerance     0 / 0.5 / 1 for urgent only / important / anything;
                            0.5 if unknown (today's in-class penalty)
        """
        start = self.sleep_start or DEFAULT_SLEEP_START
        end = self.sleep_end or DEFAULT_SLEEP_END
        now = local_time.hour + local_time.minute / 60
        hour = local_time.hour

        return {
            "campus_today": float(_DAYS[local_time.weekday()] in self.campus_days),
            "class_time_now": float(any(
                CLASS_TIME_HOURS[t][0] <= hour < CLASS_TIME_HOURS[t][1] for t in self.class_times
            )),
            "calendar_reliability": _CALENDAR_RELIABILITY.get(self.timetable_in_calendar or "", 1.0),
            "in_sleep_window": float(_in_window(now, _hours(start), _hours(end))),
            "sleep_start_hour": _hours(start),
            "sleep_end_hour": _hours(end),
            "travel_transit": float(self.travel_mode == "transit"),
            "travel_walking": float(self.travel_mode == "walking"),
            "travel_driving": float(self.travel_mode == "driving"),
            "focus_tolerance": _FOCUS_TOLERANCE.get(self.focus_interruptions or "", 0.5),
        }


def _hours(t: time) -> float:
    return t.hour + t.minute / 60


def _in_window(now: float, start: float, end: float) -> bool:
    """[start, end) on a 24-hour clock, wrapping past midnight when end <= start."""
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


class ProfileOut(BaseModel):
    """The signed-in user's profile, as the phone sees it."""

    display_name: Optional[str] = None
    onboarding_version: int = 0
    onboarding_completed_at: Optional[datetime] = None
    answers: OnboardingAnswers = Field(default_factory=OnboardingAnswers)


_NAME = Field(default=None, max_length=60)


class OnboardingIn(BaseModel):
    """POST /me/onboarding: the whole questionnaire, once, at the end of it."""

    model_config = ConfigDict(extra="forbid")

    display_name: Optional[str] = _NAME
    answers: OnboardingAnswers = Field(default_factory=OnboardingAnswers)


class ProfilePatch(BaseModel):
    """PATCH /me/profile: only what's sent changes. `answers` is merged field by
    field - {"travel_mode": "walking"} changes that one answer and keeps the rest;
    an explicit null clears one."""

    model_config = ConfigDict(extra="forbid")

    display_name: Optional[str] = _NAME
    answers: Optional[dict] = None
