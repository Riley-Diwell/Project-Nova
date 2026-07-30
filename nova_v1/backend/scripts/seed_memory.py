"""
scripts/seed_memory.py - dummy episodes for the Memory store, for testing.

WHAT THIS FILE IS
Fills `episodic_memory` (backend/db/schema.sql) with a fake week of episodes so
the Intent Surface has history to read back - loop.py's _recent_episodes() hands
Claude the last few episodes of the matching event_type, and with an empty table
there is nothing to spot patterns in.

Five weekday mornings, seven event types per day (plus one extra voice event),
all built through the real Pydantic schemas so the rows match the wire contract.
The trends planted in the data:

  - voice   : every navigation request is to the same bagel shop, ~08:10 on a
              weekday, from home - the "usual" this data exists to teach.
  - location: home -> bagel shop -> campus, same route each morning.
  - screen / accelerometer / timestamp: the same wake-and-commute rhythm.
  - notification: work chatter mid-afternoon, dismissed while DND is on.
  - calendar_trigger: the recurring 09:30 stand-up being edited.

USAGE (from backend/)
    python scripts/seed_memory.py seed        # append the dummy episodes
    python scripts/seed_memory.py status      # count rows per event_type
    python scripts/seed_memory.py clear       # DELETE every episodic_memory row
    python scripts/seed_memory.py reseed      # clear, then seed
    python scripts/seed_memory.py clear --yes # skip the confirmation prompt

Needs SUPABASE_URL / SUPABASE_SERVICE_KEY in backend/.env (see config.py).
`clear` truncates the whole table, seeded or not - it is a test-database tool.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

# Run either as `python scripts/seed_memory.py` or `python -m scripts.seed_memory`
# from backend/ - the first puts scripts/ on sys.path, not backend/, so add it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import get_client
from app.memory import TABLE, write
from app.schemas.event import (
    AccelerometerEvent,
    CalendarTriggerEvent,
    LocationEvent,
    NotificationEvent,
    ScreenEvent,
    TimeEvent,
    VoiceEvent,
)
from app.schemas.user_state import CalendarEventInfo, UserState

# The demo world is Canberra - same as navigation.py's DEFAULT_HOME/fallbacks,
# so a seeded "usual" is somewhere the Navigation tool can actually route to.
HOME = (-35.2809, 149.1300)
BAGELS = (-35.3320, 149.1580)      # Brooklyn Boy Bagels, Fyshwick
CAMPUS = (-35.2777, 149.1185)      # ANU

BAGEL_SHOP = "Brooklyn Boy Bagels, Fyshwick"

DAYS = 5  # five weekday mornings of history

# The same ask, phrased five different ways - the destination is the constant,
# so the pattern to infer is "nav request => the bagel shop", not one wording.
BAGEL_ASKS = [
    "hey nova when do I need to leave for the bagel place",
    "navigate to Brooklyn Boy Bagels",
    "how long does it take to get to the bagel shop from here",
    "take me to the bagel place",
    "directions to Brooklyn Boy Bagels please",
]

NOTIFICATIONS = [
    ("Slack", "#standup", "Reminder: post your update"),
    ("Slack", "Marta Quinn", "can you look at the PR when you get a sec"),
    ("Gmail", "ANU IT Services", "Scheduled maintenance this weekend"),
    ("Slack", "#general", "Coffee run in 10?"),
    ("WhatsApp", "Lab group", "moved the meeting to Thursday"),
]


def _loc(coords: tuple[float, float]) -> str:
    """location_ctx is the 'lat,lng' string loop.py reverse-geocodes."""
    return f"{coords[0]},{coords[1]}"


def _weekday_mornings(count: int) -> list[datetime]:
    """The `count` most recent weekdays, oldest first, at 00:00 UTC."""
    day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    days: list[datetime] = []
    while len(days) < count:
        day -= timedelta(days=1)
        if day.weekday() < 5:  # skip Sat/Sun - this is a workday routine
            days.append(day)
    return sorted(days)


def _at(day: datetime, hour: int, minute: int) -> datetime:
    return day.replace(hour=hour, minute=minute)


def _standup(day: datetime) -> CalendarEventInfo:
    """The recurring 09:30 stand-up, as UserState sees it."""
    start = _at(day, 9, 30)
    return CalendarEventInfo(
        title="Daily stand-up",
        start_millis=int(start.timestamp() * 1000),
        end_millis=int((start + timedelta(minutes=15)).timestamp() * 1000),
        location="Hanna Neumann Building",
        availability="busy",
        is_all_day=False,
        self_status="accepted",
        minutes_until_start=0,
    )


def _state(when: datetime, **overrides: Any) -> UserState:
    """A UserState at `when`, defaulting to the at-home baseline."""
    defaults: dict[str, Any] = {
        "activity": "stationary",
        "location_ctx": _loc(HOME),
        "calendar_ctx": "free",
        "dnd": False,
        "screen": False,
        "timestamp": int(when.timestamp() * 1000),
        "confidence": 0.8,
        "battery_level_percent": 74,
        "network_type": "wifi",
        "ringer_mode": "normal",
    }
    return UserState(**{**defaults, **overrides})


def _episode(
    when: datetime,
    event: Any,
    state: UserState,
    action: dict[str, Any] | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    """One episodic_memory row. created_at is set explicitly so the seeded
    history spreads across the week instead of collapsing onto now() -
    memory.recent() orders by it, so it decides what "the last few episodes"
    means.

    The seeded clock is UTC and the seeded UserState leaves utc_offset_minutes
    at 0, so a "morning" here is a morning to consolidation as well. Real
    episodes carry the phone's own offset and are converted per row; these are
    self-consistent rather than realistic on that point.
    """
    return {
        "created_at": when.isoformat(),
        "event_type": event.type,
        "event": event.model_dump(mode="json"),
        "user_state": state.model_dump(mode="json"),
        "action": action,
        "outcome": outcome,
    }


def _day_episodes(day: datetime, index: int) -> list[dict[str, Any]]:
    """One morning of the routine: wake, bagels, campus, work chatter."""
    standup = _standup(day)

    # 07:50 - idle tick before the alarm, phone face-down on the nightstand
    tick_at = _at(day, 7, 50)
    tick = _episode(
        tick_at,
        TimeEvent(id=uuid4(), timestamp=tick_at),
        _state(tick_at, motion="still", proximity_near=True, upcoming_events=[standup]),
    )

    # 08:00 - screen on, still at home
    wake_at = _at(day, 8, 0)
    wake = _episode(
        wake_at,
        ScreenEvent(id=uuid4(), timestamp=wake_at, status=True),
        _state(wake_at, screen=True, foreground_app="com.google.android.apps.messaging",
               upcoming_events=[standup]),
    )

    # 08:10 - the usual: a navigation request, always to the bagel shop
    ask_at = _at(day, 8, 10)
    bagels = _episode(
        ask_at,
        VoiceEvent(id=uuid4(), timestamp=ask_at, text=BAGEL_ASKS[index]),
        _state(ask_at, activity="walking", motion="walking", screen=True,
               calendar_ctx="busy_soon", step_count_since_boot=180 + index * 40,
               upcoming_events=[standup]),
        action={
            "tool": "navigation_departure_time",
            "params": {
                "destination": BAGEL_SHOP,
                "origin": _loc(HOME),
                "mode": "driving",
                "arrival_time": "8:30am",
            },
        },
        outcome="accepted",  # they went, every time
    )

    # 08:25 - arrived at the bagel shop
    arrive_at = _at(day, 8, 25)
    arrived = _episode(
        arrive_at,
        LocationEvent(id=uuid4(), timestamp=arrive_at, lat=BAGELS[0], lng=BAGELS[1]),
        _state(arrive_at, activity="walking", motion="walking",
               location_ctx=_loc(BAGELS), calendar_ctx="busy_soon",
               screen=False, upcoming_events=[standup]),
    )

    # 08:40 - driving on to campus for the stand-up
    drive_at = _at(day, 8, 40)
    drive = _episode(
        drive_at,
        AccelerometerEvent(id=uuid4(), timestamp=drive_at, threshold="driving"),
        _state(drive_at, activity="driving", motion="in_vehicle",
               location_ctx=_loc(BAGELS), calendar_ctx="busy_soon",
               bluetooth_audio_connected=True, music_active=True,
               upcoming_events=[standup]),
    )

    # 09:20 - someone shuffles the stand-up again
    cal_at = _at(day, 9, 20)
    start = _at(day, 9, 30) + timedelta(minutes=index * 5)
    calendar = _episode(
        cal_at,
        CalendarTriggerEvent(
            id=uuid4(),
            timestamp=cal_at,
            calendar_event_id=f"standup-{day:%Y%m%d}",
            calendar_event_name="Daily stand-up",
            calendar_event_duration=0.25,
            calendar_event_start=start,
            calendar_event_end=start + timedelta(minutes=15),
            calendar_event_location="Hanna Neumann Building",
        ),
        _state(cal_at, activity="stationary", location_ctx=_loc(CAMPUS),
               calendar_ctx="busy_soon", upcoming_events=[standup]),
    )

    # 14:35 - work chatter while heads-down; dismissed every time
    notify_at = _at(day, 14, 35)
    app, title, body = NOTIFICATIONS[index]
    notification = _episode(
        notify_at,
        NotificationEvent(id=uuid4(), timestamp=notify_at, app=app, title=title, body=body),
        _state(notify_at, activity="stationary", location_ctx=_loc(CAMPUS),
               calendar_ctx="in_event", dnd=True, interruption_filter="priority",
               ringer_mode="silent", foreground_app="com.google.android.calendar",
               current_events=[standup]),
        action={"tool": "notification_management", "params": {"decision": "hold"}},
        outcome="rejected",  # never wants these surfaced mid-meeting
    )

    return [tick, wake, bagels, arrived, drive, calendar, notification]


def build_episodes() -> list[dict[str, Any]]:
    """Every dummy episode, oldest first."""
    days = _weekday_mornings(DAYS)
    episodes: list[dict[str, Any]] = []
    for index, day in enumerate(days):
        episodes.extend(_day_episodes(day, index))

    # A non-navigation voice event on the most recent day, so "voice" isn't
    # uniformly the bagel run - the trend should be strong, not absolute.
    ask_at = _at(days[-1], 17, 40)
    episodes.append(_episode(
        ask_at,
        VoiceEvent(id=uuid4(), timestamp=ask_at, text="what's on my calendar tomorrow"),
        _state(ask_at, activity="stationary", location_ctx=_loc(CAMPUS), screen=True),
    ))
    return sorted(episodes, key=lambda e: e["created_at"])


# --- commands ---------------------------------------------------------------

def seed() -> None:
    episodes = build_episodes()
    for episode in episodes:
        write(episode)
    print(f"seeded {len(episodes)} episodes into {TABLE}")
    status()


def clear(assume_yes: bool = False) -> bool:
    """Delete every row in episodic_memory. Test databases only.
    Returns False if the user declined at the prompt."""
    if not assume_yes:
        answer = input(f"delete ALL rows in {TABLE}? type 'clear' to confirm: ")
        if answer.strip().lower() != "clear":
            print("aborted - nothing deleted")
            return False

    # PostgREST refuses an unfiltered delete, so match every real uuid.
    deleted = (
        get_client()
        .table(TABLE)
        .delete()
        .neq("id", "00000000-0000-0000-0000-000000000000")
        .execute()
        .data
    )
    print(f"deleted {len(deleted)} rows from {TABLE}")
    return True


def status() -> None:
    """Row counts per event_type - what the Intent Surface has to read back."""
    rows = get_client().table(TABLE).select("event_type").execute().data
    if not rows:
        print(f"{TABLE} is empty")
        return

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["event_type"]] = counts.get(row["event_type"], 0) + 1
    print(f"{TABLE}: {len(rows)} rows")
    for event_type, count in sorted(counts.items()):
        print(f"  {event_type:<18} {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("command", choices=["seed", "clear", "reseed", "status"])
    parser.add_argument("--yes", action="store_true",
                        help="skip the confirmation prompt on clear/reseed")
    args = parser.parse_args()

    if args.command == "seed":
        seed()
    elif args.command == "clear":
        clear(args.yes)
    elif args.command == "reseed":
        if clear(args.yes):
            seed()
    else:
        status()


if __name__ == "__main__":
    main()
