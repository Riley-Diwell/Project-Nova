"""ask_choice: the model asks the user to pick one of 2-3 options, the turn ends
on the question, and the pick (the label itself, as the next voice turn)
continues the same thread a spoken answer would."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import intent_surface
from app.control.commands import Command
from app.control.controller import ProportionalController
from app.control.observer import Observation
from app.schemas.event_out import EventOut

USER = uuid4()


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setattr(intent_surface, "_PENDING_CONFIRMATION", {})
    turn = ProportionalController(intent_surface._REGISTRY).open_turn(Observation(), Command(text="move it"))
    return intent_surface.TurnContext(turn=turn, user_id=USER, utc_offset_minutes=600)


def _reply(finish_reason, content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content, tool_calls=tool_calls),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, prompt_tokens_details=None),
    )


def _call(call_id, name, args):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def _model(monkeypatch, *replies):
    """Fakes the model server; returns the message lists it was sent."""
    queue = iter(replies)
    seen = []

    def create(**kw):
        seen.append(kw["messages"])
        return next(queue)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(intent_surface.llm, "client", lambda *a, **kw: fake)
    return seen


ASK = {"question": "Which time works for the dentist?", "options": ["Thursday at 10", "Friday at 2"]}


def test_ask_choice_is_offered_on_every_turn():
    names = [t["function"]["name"] for t in intent_surface._build_tools([])]
    assert "ask_choice" in names


def test_asking_ends_the_turn_on_the_question(ctx, monkeypatch):
    _model(monkeypatch, _reply("tool_calls", tool_calls=[_call("c1", "ask_choice", ASK)]))
    result = intent_surface._run_loop([{"role": "user", "content": "{}"}], 3, uuid4(), ctx, is_voice=True)
    assert result.speech == "Which time works for the dentist?"
    assert result.confirmation == "choice"
    assert result.options == ["Thursday at 10", "Friday at 2"]
    # Not an Action: it changes nothing, so it isn't logged as one.
    assert all(a.tool != "ask_choice" for a in ctx.actions)
    out = EventOut(event_id=result.event_id, speech=result.speech,
                   confirmation=result.confirmation, options=result.options)
    assert out.options == ["Thursday at 10", "Friday at 2"]


def test_the_pick_continues_the_stashed_thread(ctx, monkeypatch):
    _model(monkeypatch, _reply("tool_calls", tool_calls=[_call("c1", "ask_choice", ASK)]))
    intent_surface._run_loop([{"role": "user", "content": "move my dentist reminder"}], 3, uuid4(), ctx, is_voice=True)

    thread = intent_surface._pop_pending_confirmation(USER)
    assert thread is not None
    # A valid thread for the model server: the call is answered, then the question.
    assert thread[-3]["tool_calls"][0]["id"] == "c1"
    assert thread[-2]["role"] == "tool" and thread[-2]["tool_call_id"] == "c1"
    assert thread[-1] == {"role": "assistant", "content": "Which time works for the dentist?"}

    # The next voice turn is the label itself, on the end of that thread - what
    # run() does with a carried thread for a spoken answer too.
    seen = _model(monkeypatch, _reply("stop", content="Moved it to Friday at 2."))
    answer = [*thread, {"role": "user", "content": "Friday at 2"}]
    result = intent_surface._run_loop(answer, 3, uuid4(), ctx, is_voice=True)
    assert result.speech == "Moved it to Friday at 2."
    sent = seen[0][1:]  # past the system prompt
    assert sent[:-1] == thread and sent[-1]["content"] == "Friday at 2"


@pytest.mark.parametrize("options", [
    ["Thursday at 10"],
    ["Thursday at 10", "thursday  at 10"],  # the same option twice is one option
    ["a", "b", "c", "d"],  # 1 press repeats, so 4 options would need 5 presses
    ["Thursday at 10", "x" * 61],
])
def test_bad_options_go_back_to_the_model(ctx, monkeypatch, options):
    seen = _model(
        monkeypatch,
        _reply("tool_calls", tool_calls=[_call("c1", "ask_choice", {"question": "Which?", "options": options})]),
        _reply("stop", content="When would you like it?"),
    )
    result = intent_surface._run_loop([{"role": "user", "content": "{}"}], 3, uuid4(), ctx, is_voice=True)
    assert result.confirmation == "open"
    assert result.options is None
    refusal = json.loads(seen[1][-1]["content"])
    assert refusal["success"] is False


def test_an_ambient_turn_cannot_ask(ctx, monkeypatch):
    seen = _model(
        monkeypatch,
        _reply("tool_calls", tool_calls=[_call("c1", "ask_choice", ASK)]),
        _reply("stop", content=""),
    )
    result = intent_surface._run_loop([{"role": "user", "content": "{}"}], 3, uuid4(), ctx, is_voice=False)
    assert result.confirmation is None and result.options is None
    assert json.loads(seen[1][-1]["content"])["success"] is False
    assert intent_surface._pop_pending_confirmation(USER) is None
