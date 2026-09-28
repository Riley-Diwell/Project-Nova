"""A DEVICE_TOOLS call that rejects its own input is recorded with ran=False.

The phone skips ran=false Actions (NovaApiClient.inputsFor), so this is what
stops it carrying out an instruction the tool itself refused. Every other tool
keeps its current behaviour: ran=True whatever it returned.
"""
from datetime import datetime, timezone

import pytest

from app import intent_surface
from app.control.commands import Command
from app.control.controller import ProportionalController
from app.control.observer import Observation
from app.tools.core.catalogue import DEVICE_TOOLS


@pytest.fixture
def ctx():
    # A commanded turn, so every tool is authorised without consulting gain.
    turn = ProportionalController(intent_surface._REGISTRY).open_turn(Observation(), Command(text="do it"))
    return intent_surface.TurnContext(
        turn=turn, location_ctx="-35.28,149.13", preferred_travel_mode="walking",
        utc_offset_minutes=600,
    )


def fake_dispatch(result):
    return lambda name, tool_input: result


def test_rejected_device_tool_is_not_run(ctx, monkeypatch):
    monkeypatch.setattr(intent_surface._DISPATCHER, "dispatch_reactive",
                        fake_dispatch({"success": False, "error": "nope"}))
    intent_surface._run_local_tool("set_reminder", {"text": "x"}, ctx)
    assert [a.ran for a in ctx.actions] == [False]


def test_accepted_device_tool_is_run(ctx, monkeypatch):
    monkeypatch.setattr(intent_surface._DISPATCHER, "dispatch_reactive",
                        fake_dispatch({"success": True}))
    intent_surface._run_local_tool("update_reminder", {"reminder_id": "x"}, ctx)
    assert [a.ran for a in ctx.actions] == [True]


@pytest.mark.parametrize("tool", ["memory", "navigation_departure_time", "add_calendar_event"])
def test_other_tools_keep_ran_true_on_failure(ctx, monkeypatch, tool):
    monkeypatch.setattr(intent_surface._DISPATCHER, "dispatch_reactive",
                        fake_dispatch({"success": False, "error": "nope"}))
    intent_surface._run_local_tool(tool, {}, ctx)
    assert [a.ran for a in ctx.actions] == [True]


def test_device_tools_are_not_given_the_users_location(ctx, monkeypatch):
    monkeypatch.setattr(intent_surface._DISPATCHER, "dispatch_reactive",
                        fake_dispatch({"success": True}))
    for tool in sorted(DEVICE_TOOLS):
        intent_surface._run_local_tool(tool, {}, ctx)
    for action in ctx.actions:
        assert "origin" not in action.input
        assert "mode" not in action.input
        # Still needed to judge whether a due_local has passed.
        assert action.input["utc_offset_minutes"] == 600


def test_navigation_still_gets_origin_and_mode(ctx, monkeypatch):
    monkeypatch.setattr(intent_surface._DISPATCHER, "dispatch_reactive",
                        fake_dispatch({"success": True}))
    intent_surface._run_local_tool("navigation_departure_time", {"destination": "CSIT"}, ctx)
    assert ctx.actions[0].input["origin"] == "-35.28,149.13"
    assert ctx.actions[0].input["mode"] == "walking"


def test_stored_reminder_lists_are_hidden_from_the_model():
    row = {
        "created_at": datetime(2026, 9, 22, tzinfo=timezone.utc).isoformat(),
        "event": {"type": "voice", "text": "remind me"},
        "user_state": {"utc_offset_minutes": 600, "reminders": [{"id": "old"}],
                       "reminders_pending_total": 3},
        "action": {"actions": []},
    }
    shown = intent_surface._for_model_episode(row)
    assert "reminders" not in shown["user_state"]
    assert "reminders_pending_total" not in shown["user_state"]
