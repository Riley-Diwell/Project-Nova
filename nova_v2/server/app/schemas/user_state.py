"""
schemas/user_state.py - Section 5.2: State Estimator  (Georgia)

STATUS: working draft

WHAT THIS FILE IS
Defines the UserState schema - the wire shape Android computes on device 
and posts directly to POST /event alongside every Event.

Everything below `confidence` is optional because it's permission-gated or
sensor-dependent on the Android side - older clients or the demo fallback
declared state may omit them.

WHO USES THIS
- Riley: Android posts this alongside every Event to POST /event
- intent_surface.py: consumes it as turn context; control/observer.py predicts from it
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class CalendarEventInfo(BaseModel):
    """Mirrors android's model/CalendarEventInfo.kt."""
    title: str
    start_millis: int
    end_millis: int
    start_local: Optional[str] = None
    end_local: Optional[str] = None
    location: Optional[str] = None
    availability: str          # "busy" / "free" / "tentative"
    is_all_day: bool
    self_status: str           # "accepted" / "declined" / "tentative" / "none"
    minutes_until_start: int   # negative = currently in progress
    event_id: int              # CalendarContract.Events._ID - what delete_calendar_event targets


class ReminderInfo(BaseModel):
    """One reminder in the window the phone attaches to voice turns - mirrors
    android's model/Reminder.kt ReminderSummary. The phone owns reminders; this
    is a read-only view of them for the model (tools/functions/reminder_tool.py).
    """
    id: str
    text: str
    due_local: str                          # the user's wall clock, no suffix
    minutes_until_due: int                  # negative = overdue
    status: str                             # pending / snoozed / deferred / fired
    priority: str = "normal"
    fired_minutes_ago: Optional[int] = None
    recurrence: Optional[str] = None        # e.g. "weekly", for a repeating one


class UserState(BaseModel):
    """
    Deterministic summary of user state, computed on-device by Android's
    UserStateCollector and posted as-is alongside every Event.
    """
    # Frozen seam (DESIGN.md Sections 5.2/6)
    activity: Optional[str] = None
    location_ctx: Optional[str] = None
    calendar_ctx: Optional[str] = None   # "free" / "in_event" / "busy_soon"
    dnd: bool = False
    screen: bool = False
    timestamp: int = 0                   # epoch millis
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    utc_offset_minutes: int = 0

    # Phase 2 sensor-inference extension
    motion: Optional[str] = None
    ambient_light_lux: Optional[float] = None
    proximity_near: Optional[bool] = None
    network_type: Optional[str] = None
    battery_level_percent: Optional[int] = None
    battery_charging: Optional[bool] = None
    wired_headset_connected: Optional[bool] = None
    bluetooth_audio_connected: Optional[bool] = None

    # No-permission signals
    ringer_mode: Optional[str] = None
    music_active: Optional[bool] = None
    interruption_filter: Optional[str] = None
    screen_orientation: Optional[str] = None
    power_save_mode: Optional[bool] = None
    airplane_mode: Optional[bool] = None

    # Permissioned signals
    step_count_since_boot: Optional[int] = None
    call_state: Optional[str] = None
    foreground_app: Optional[str] = None

    # Rich calendar detail
    current_events: List[CalendarEventInfo] = []
    upcoming_events: List[CalendarEventInfo] = []

    # Declared (not inferred) in Settings - navigation_departure_time's default
    # mode when the model doesn't name one. "transit" / "walking" / "driving".
    preferred_travel_mode: Optional[str] = None

    # Reminders (additive). Only attached to voice turns - ambient snapshots
    # leave these empty - and never stored in the Episode (main.py).
    # `reminders` is a bounded window: fired in the last 12h, snoozed/deferred,
    # and pending within 7 days, soonest first. The total counts every active
    # reminder, so the model knows when the window is not the whole list.
    reminders: List[ReminderInfo] = []
    reminders_pending_total: Optional[int] = None
