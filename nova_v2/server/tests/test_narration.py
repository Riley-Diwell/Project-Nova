"""Audit-log sentences for the reminder tools, for both values of ran."""
import pytest

from app.tools.core.narration import describe_action


def test_set_reminder_at_a_time():
    text = describe_action("set_reminder", {"text": "Email Dr Chen",
                                            "due_local": "2026-09-23T16:30:00"}, True)
    assert text == "Set a reminder to 'Email Dr Chen' for Wed, Sep 23, 4:30pm."


def test_set_reminder_relative():
    text = describe_action("set_reminder", {"text": "Stretch", "in_minutes": 90}, True)
    assert text == "Set a reminder to 'Stretch' in 1 hour 30 minutes."


def test_set_reminder_recurring():
    text = describe_action("set_reminder", {"text": "Timesheet", "due_local": "2026-09-28T09:00:00",
                                            "recurrence": {"frequency": "weekly"}}, True)
    assert text.endswith(", repeating weekly.")


def test_set_reminder_not_run():
    text = describe_action("set_reminder", {"text": "Email Dr Chen"}, False)
    assert text == "Considered setting a reminder to 'Email Dr Chen', but didn't."


@pytest.mark.parametrize("action, expected", [
    ({"action": "complete"}, "Marked 'Email Dr Chen' done."),
    ({"action": "snooze", "in_minutes": 10}, "Snoozed 'Email Dr Chen' for 10 minutes."),
    ({"action": "snooze", "in_minutes": 60}, "Snoozed 'Email Dr Chen' for 1 hour."),
    ({"action": "edit", "shift_minutes": 30}, "Changed the reminder 'Email Dr Chen'."),
    ({"action": "delete"}, "Removed the reminder 'Email Dr Chen'."),
])
def test_update_reminder(action, expected):
    assert describe_action("update_reminder", {"label": "Email Dr Chen", **action}, True) == expected


def test_update_reminder_not_run():
    text = describe_action("update_reminder", {"label": "Email Dr Chen", "action": "delete"}, False)
    assert text == "Considered removing the reminder 'Email Dr Chen', but didn't."


def test_get_reminders():
    text = describe_action("get_reminders", {"from_time": "2026-10-05T00:00:00",
                                             "to_time": "2026-10-12T00:00:00"}, True)
    assert text.startswith("Checked your reminders for Mon, Oct 5")
