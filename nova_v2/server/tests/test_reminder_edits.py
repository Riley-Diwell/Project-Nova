"""'Change my ... reminder to ...' - the edit path end to end on the server.

Classified as a command, validated by update_reminder, and refused when it
names a reminder the model was never shown (the phone would silently ignore
it, after the model had already said "done").
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app import intent_surface
from app.control.commands import Command, classify
from app.control.controller import ProportionalController
from app.control.observer import Observation
from app.schemas.event import VoiceEvent
from app.tools.functions import reminder_tool
from app.tools.functions.reminder_tool import UpdateReminderTool

SHOWN = "5b0f3c1e-2d4a-4c8e-9f1a-0123456789ab"
UNSEEN = "0d9c6b2a-1111-4222-8333-444455556666"


def voice(text):
    return VoiceEvent(id=uuid4(), timestamp=datetime.now(timezone.utc), text=text)


@pytest.mark.parametrize("text", [
    "change my dentist reminder to 4pm",
    "move the form reminder to Friday",
    "make the Sam one say call Sam instead",
    "push my gym reminder back an hour",
    "hey nova, can you change the milk reminder to tomorrow",
])
def test_edit_requests_are_commands(text):
    assert classify(voice(text)) is not None


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setattr(reminder_tool, "_now_utc",
                        lambda: datetime(2026, 9, 23, 6, 0, tzinfo=timezone.utc))
    turn = ProportionalController(intent_surface._REGISTRY).open_turn(Observation(), Command(text="change it"))
    return intent_surface.TurnContext(turn=turn, utc_offset_minutes=600,
                                      known_reminder_ids={SHOWN})


def edit(ctx, reminder_id, **change):
    return intent_surface._run_local_tool(
        "update_reminder",
        {"reminder_id": reminder_id, "action": "edit", "label": "Dentist", **change},
        ctx,
    )


def test_a_new_time_on_a_shown_reminder_runs(ctx):
    result = edit(ctx, SHOWN, due_local="2026-09-24T16:00:00")
    assert result["success"] is True
    assert ctx.actions[-1].ran is True
    assert ctx.actions[-1].input["due_local"] == "2026-09-24T16:00:00"


def test_new_wording_runs(ctx):
    assert edit(ctx, SHOWN, text="Call Sam instead")["success"] is True


def test_an_unseen_id_is_refused_with_a_way_forward(ctx):
    result = edit(ctx, UNSEEN, due_local="2026-09-24T16:00:00")
    assert result["success"] is False
    assert "get_reminders" in result["error"]
    assert ctx.actions[-1].ran is False


def test_no_window_from_the_phone_turns_the_check_off(ctx):
    ctx.known_reminder_ids = None
    assert edit(ctx, UNSEEN, shift_minutes=60)["success"] is True


def test_other_tools_are_not_checked(ctx):
    result = intent_surface._run_local_tool(
        "set_reminder", {"text": "x", "in_minutes": 5}, ctx)
    assert result["success"] is True


def test_get_reminders_result_widens_the_known_ids(ctx, monkeypatch):
    # resume() would call the model; stop just before it.
    monkeypatch.setattr(intent_surface, "_run_loop", lambda messages, n, event_id, c, is_voice: c)
    intent_surface._PENDING_SESSIONS["s1"] = {
        "messages": [], "tool_use_id": "t1", "event_id": uuid4(), "ctx": ctx,
        "user_id": "u1", "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    resumed = intent_surface.resume("u1", "s1", {"reminders": [{"id": UNSEEN, "text": "Dentist"}]})
    assert resumed.known_reminder_ids == {SHOWN, UNSEEN}


def test_the_edit_description_covers_change_to():
    description = UpdateReminderTool().description
    assert "Change my X reminder to 4pm" in description
