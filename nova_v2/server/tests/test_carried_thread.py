"""A thread carried into a follow-up (intent_surface._compact_thread) keeps the
conversation but not the bulk: earlier context, long tool results and long
call arguments are cut, so a "yes" doesn't re-read the last turn's Canvas."""
import json

from app import intent_surface


def _thread():
    payload = {"event": {"type": "voice", "text": "what's due in COMP3310?"},
               "user_state": {"reminders": ["x"] * 200}, "local_time": "2026-10-28T09:00:00",
               "persona": [{"text": "likes tea"}], "recent_episodes": [{"a": 1}] * 20}
    note = "## Week 9\n" + "- point\n" * 300
    return [
        {"role": "user", "content": json.dumps(payload)},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "canvas", "arguments": '{"action": "upcoming"}'}},
            {"id": "c2", "type": "function", "function": {
                "name": "memory", "arguments": json.dumps({"action": "save", "title": "Week 9", "text": note})}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"due": ["Lab 4"] * 400})},
        {"role": "tool", "tool_call_id": "c2", "content": '{"success": true}'},
        {"role": "assistant", "content": "Lab 4 is due Friday. Want a reminder?"},
    ]


def test_the_conversation_survives():
    out = intent_surface._compact_thread(_thread())
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "tool", "assistant"]
    assert [c["id"] for c in out[1]["tool_calls"]] == ["c1", "c2"]
    assert [m.get("tool_call_id") for m in out[2:4]] == ["c1", "c2"]
    assert out[-1]["content"] == "Lab 4 is due Friday. Want a reminder?"
    assert json.loads(out[0]["content"])["event"]["text"] == "what's due in COMP3310?"


def test_the_bulk_is_cut():
    thread = _thread()
    out = intent_surface._compact_thread(thread)
    assert "user_state" not in json.loads(out[0]["content"])
    assert len(out[2]["content"]) < 600 and "excerpt" in json.loads(out[2]["content"])
    assert out[3]["content"] == '{"success": true}'  # short results stay whole
    args = json.loads(out[1]["tool_calls"][1]["function"]["arguments"])
    assert args["title"] == "Week 9" and len(args["text"]) < 400
    # The live thread isn't touched - only the stashed copy.
    assert "user_state" in json.loads(thread[0]["content"])


def test_stashing_compacts(monkeypatch):
    monkeypatch.setattr(intent_surface, "_PENDING_CONFIRMATION", {})
    intent_surface._stash_pending_confirmation("u1", _thread())
    carried = intent_surface._pop_pending_confirmation("u1")
    assert "user_state" not in json.loads(carried[0]["content"])
