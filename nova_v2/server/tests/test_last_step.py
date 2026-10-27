"""A turn's last step answers from what it has (intent_surface._loop): no more
tool calls, so a model still searching says "I couldn't find that" instead of
the turn ending on STUCK_SPEECH."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import intent_surface
from app.control.commands import Command
from app.control.controller import ProportionalController
from app.control.observer import Observation

USER = uuid4()


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setattr(intent_surface, "_PENDING_CONFIRMATION", {})
    turn = ProportionalController(intent_surface._REGISTRY).open_turn(
        Observation(), Command(text="how much is my assignment worth"))
    return intent_surface.TurnContext(turn=turn, user_id=USER, utc_offset_minutes=600)


def _reply(finish_reason, content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content, tool_calls=tool_calls),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, prompt_tokens_details=None),
    )


def _search(call_id):
    # Arguments that aren't JSON: answered with _BAD_ARGUMENTS, nothing runs.
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name="web_search", arguments="nope"))


def _model(monkeypatch, *replies):
    queue = iter(replies)
    sent = []

    def create(**kw):
        sent.append(kw)
        return next(queue)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(intent_surface.llm, "client", lambda *a, **kw: fake)
    return sent


def test_the_last_step_may_not_call_a_tool(ctx, monkeypatch):
    sent = _model(
        monkeypatch,
        _reply("tool_calls", tool_calls=[_search("c1")]),
        _reply("tool_calls", tool_calls=[_search("c2")]),
        _reply("stop", content="I couldn't find the weighting in Canvas."),
    )
    result = intent_surface._run_loop([{"role": "user", "content": "{}"}], 3, uuid4(), ctx, is_voice=True)
    assert result.speech == "I couldn't find the weighting in Canvas."
    assert [kw["tool_choice"] for kw in sent] == ["auto", "auto", "none"]
    # Told why, on that request only - and the tools stay, for the prefix cache.
    assert sent[-1]["messages"][-1] == {"role": "user", "content": intent_surface._LAST_STEP}
    assert sent[-1]["tools"] == sent[0]["tools"]
    assert all(m.get("content") != intent_surface._LAST_STEP for m in sent[0]["messages"])


def test_a_call_on_the_last_step_is_dropped(ctx, monkeypatch):
    _model(
        monkeypatch,
        _reply("tool_calls", tool_calls=[_search("c1")]),
        _reply("tool_calls", content="It isn't listed in Canvas.", tool_calls=[_search("c2")]),
    )
    result = intent_surface._run_loop([{"role": "user", "content": "{}"}], 2, uuid4(), ctx, is_voice=True)
    assert result.speech == "It isn't listed in Canvas."


def test_a_phone_lookup_does_not_reset_the_step_budget(ctx, monkeypatch):
    calendar = SimpleNamespace(id="cal", function=SimpleNamespace(
        name="get_calendar_range",
        arguments='{"from_time": "2026-10-28T00:00", "to_time": "2026-10-29T00:00"}'))
    _model(
        monkeypatch,
        _reply("tool_calls", tool_calls=[_search("c1")]),
        _reply("tool_calls", tool_calls=[calendar]),
    )
    paused = intent_surface._run_loop([{"role": "user", "content": "{}"}], 5, uuid4(), ctx, is_voice=True)
    assert isinstance(paused, intent_surface.NeedMoreResult)

    # Two of five steps went before the hop, so three are left after it.
    sent = _model(
        monkeypatch,
        _reply("tool_calls", tool_calls=[_search("c2")]),
        _reply("tool_calls", tool_calls=[_search("c3")]),
        _reply("stop", content="You're free all day."),
    )
    result = intent_surface.resume(USER, paused.session_id, {"events": []})
    assert result.speech == "You're free all day."
    assert [kw["tool_choice"] for kw in sent] == ["auto", "auto", "none"]
