"""set_reminder / update_reminder / get_reminders input validation.

The tools validate their own input and return {"success": False, "error": ...}
rather than raising, so the model can re-ask. The clock is pinned through
reminder_tool._now_utc.
"""
from datetime import datetime, timezone

import pytest

from app.tools.core.schema import ToolSchema
from app.tools.functions import reminder_tool
from app.tools.functions.reminder_tool import (
    GetRemindersTool,
    SetReminderTool,
    UpdateReminderTool,
)

# 2026-09-23 06:00 UTC is 16:00 in Canberra (AEST, +600).
NOW_UTC = datetime(2026, 9, 23, 6, 0, tzinfo=timezone.utc)
OFFSET = 600
REMINDER_ID = "5b0f3c1e-2d4a-4c8e-9f1a-0123456789ab"


@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch):
    monkeypatch.setattr(reminder_tool, "_now_utc", lambda: NOW_UTC)


def set_reminder(**kwargs):
    return SetReminderTool().invoke({"utc_offset_minutes": OFFSET, **kwargs})


def update_reminder(**kwargs):
    base = {"reminder_id": REMINDER_ID, "label": "Email Dr Chen", "utc_offset_minutes": OFFSET}
    return UpdateReminderTool().invoke({**base, **kwargs})


@pytest.mark.parametrize("tool", [SetReminderTool(), UpdateReminderTool(), GetRemindersTool()])
def test_schema_is_accepted(tool):
    schema = ToolSchema(
        name=tool.name, description=tool.description,
        input_schema=tool.input_schema, gain=0.2,
    )
    assert schema.input_schema["type"] == "object"


# --- set_reminder ------------------------------------------------------------

def test_set_with_local_time():
    result = set_reminder(text="Email Dr Chen", due_local="2026-09-23T16:30:00")
    assert result["success"] is True
    assert result["due_local"] == "2026-09-23T16:30:00"
    assert result["priority"] == "normal"


def test_set_with_in_minutes():
    result = set_reminder(text="Take the pasta off", in_minutes=20)
    assert result["success"] is True
    assert result["in_minutes"] == 20


def test_text_required():
    assert set_reminder(text="  ", in_minutes=5)["success"] is False


@pytest.mark.parametrize("timing", [{}, {"due_local": "2026-09-23T17:00:00", "in_minutes": 5}])
def test_exactly_one_of_due_local_and_in_minutes(timing):
    assert set_reminder(text="x", **timing)["success"] is False


@pytest.mark.parametrize("due", ["tomorrow at 9", "2026-13-01T09:00:00", ""])
def test_malformed_iso_is_rejected(due):
    assert set_reminder(text="x", due_local=due)["success"] is False


@pytest.mark.parametrize("due", ["2026-09-23T17:00:00Z", "2026-09-23T17:00:00+10:00"])
def test_timezone_suffix_is_rejected(due):
    result = set_reminder(text="x", due_local=due)
    assert result["success"] is False
    assert "LOCAL" in result["error"]


def test_past_time_is_rejected_against_the_users_clock():
    # 09:00 local has passed at 16:00 local, even though 09:00 UTC hasn't.
    result = set_reminder(text="x", due_local="2026-09-23T09:00:00")
    assert result["success"] is False
    assert "passed" in result["error"]


def test_a_minute_ago_is_tolerated():
    assert set_reminder(text="x", due_local="2026-09-23T15:59:00")["success"] is True


@pytest.mark.parametrize("minutes", [0, -5, 527_041, True, 2.5])
def test_in_minutes_bounds(minutes):
    assert set_reminder(text="x", in_minutes=minutes)["success"] is False


def test_in_minutes_upper_bound_is_inclusive():
    assert set_reminder(text="x", in_minutes=527_040)["success"] is True


def test_priority_enum():
    assert set_reminder(text="x", in_minutes=5, priority="urgent")["success"] is False
    assert set_reminder(text="x", in_minutes=5, priority="important")["priority"] == "important"


def test_recurrence_validated():
    ok = set_reminder(text="Submit timesheet", due_local="2026-09-28T09:00:00",
                      recurrence={"frequency": "weekly"})
    assert ok["success"] is True
    assert ok["recurrence"] == {"frequency": "weekly"}
    bad = set_reminder(text="x", due_local="2026-09-28T09:00:00",
                       recurrence={"frequency": "hourly"})
    assert bad["success"] is False
    both = set_reminder(text="x", due_local="2026-09-28T09:00:00",
                        recurrence={"frequency": "daily", "count": 3, "until": "2026-10-10T00:00:00"})
    assert both["success"] is False


# --- update_reminder ---------------------------------------------------------

@pytest.mark.parametrize("bad_id", ["1", "reminder-1", "", None])
def test_reminder_id_must_be_a_uuid(bad_id):
    assert update_reminder(reminder_id=bad_id, action="complete")["success"] is False


def test_label_required():
    assert update_reminder(action="complete", label="")["success"] is False


def test_unknown_action_rejected():
    assert update_reminder(action="archive")["success"] is False


@pytest.mark.parametrize("action", ["complete", "delete"])
def test_complete_and_delete_need_nothing_else(action):
    result = update_reminder(action=action)
    assert result["success"] is True
    assert result["reminder_id"] == REMINDER_ID


def test_snooze_defaults_to_ten_minutes():
    assert update_reminder(action="snooze")["in_minutes"] == 10


def test_snooze_with_minutes():
    assert update_reminder(action="snooze", in_minutes=60)["in_minutes"] == 60
    assert update_reminder(action="snooze", in_minutes=0)["success"] is False


def test_edit_that_changes_nothing_is_rejected():
    assert update_reminder(action="edit")["success"] is False


def test_edit_takes_one_timing_field_only():
    result = update_reminder(action="edit", shift_minutes=30, in_minutes=10)
    assert result["success"] is False


@pytest.mark.parametrize("change", [
    {"text": "Email Dr Chen and cc Sam"},
    {"due_local": "2026-09-24T09:00:00"},
    {"in_minutes": 45},
    {"shift_minutes": -60},
    {"recurrence": {"frequency": "daily"}},
])
def test_edit_accepts_each_kind_of_change(change):
    result = update_reminder(action="edit", **change)
    assert result["success"] is True
    for key, value in change.items():
        assert result[key] == value


def test_edit_rejects_a_past_time_and_zero_shift():
    assert update_reminder(action="edit", due_local="2026-09-23T08:00:00")["success"] is False
    assert update_reminder(action="edit", shift_minutes=0)["success"] is False


def test_get_reminders_is_a_client_tool_and_says_so_if_reached():
    result = GetRemindersTool().invoke({"from_time": "2026-10-05T00:00:00",
                                       "to_time": "2026-10-12T00:00:00"})
    assert result["success"] is False
