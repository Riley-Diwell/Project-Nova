"""The line said while a turn runs (control/ack.py, POST /event/ack)."""
import pytest

from app.control import ack, commands
from app.core import llm


@pytest.fixture(autouse=True)
def model_unreachable(monkeypatch):
    """The keyword fallback, unless a test hands the model an answer."""
    def unreachable(*a, **k):
        raise ConnectionError

    monkeypatch.setattr(llm, "parse", unreachable)
    monkeypatch.setattr(ack, "MOCK_LLM", False)


def test_chat_gets_no_line():
    assert ack.phrase("I'm so tired today") is None
    assert ack.phrase("yes") is None
    assert ack.phrase("") is None


def test_nothing_to_check_gets_no_line():
    # Questions and requests, but nothing to look up first.
    assert ack.phrase("How are you?") is None
    assert ack.phrase("tell me a joke") is None
    assert ack.phrase("what do you think of jazz") is None


def test_a_question_is_answered_with_what_nova_is_checking():
    calendar = ack._TOPICS["calendar"][1]
    assert ack.phrase("what's on my calendar tomorrow") in calendar
    assert ack.phrase("am I free at 3") in calendar
    assert ack.phrase("do I have any reminders today?") in ack._TOPICS["reminders"][1]


def test_an_instruction_is_answered_with_what_nova_is_doing():
    assert ack.phrase("remind me to call mum at 5") in ack._TOPICS["reminders"][2]
    assert ack.phrase("can you add coffee with Sam to my calendar?") in ack._TOPICS["calendar"][2]
    assert ack.phrase("set a timer for ten minutes") in ack._TOPICS["alarms"][2]


def test_the_first_topic_wins():
    # A reminder about a meeting is a reminder, not a calendar check.
    assert ack.phrase("remind me about the meeting") in ack._TOPICS["reminders"][2]


def test_the_line_names_what_was_asked_about():
    assert ack.phrase("How much do my Network Security assignments weigh?") in ack._TOPICS["weighting"][1]
    assert ack.phrase("what mark did I get on my last lab") in ack._TOPICS["marks"][1]
    assert ack.phrase("what's in this week's tutorial?") in ack._TOPICS["course material"][1]
    assert ack.phrase("when's my COMP3310 exam?") in ack._TOPICS["coursework"][1]


def test_how_did_i_go_is_asking_for_a_mark():
    marks = ack._TOPICS["marks"][1]
    assert ack.phrase("How'd I go on the first written testt for power systems?") in marks
    assert ack.phrase("how did I go in the physics quiz") in marks


def test_a_plural_or_a_typo_still_matches():
    assert ack.phrase("when are my tests?") in ack._TOPICS["coursework"][1]
    assert ack.phrase("whens the testt") in ack._TOPICS["coursework"][1]


def test_whats_coming_up_without_saying_where():
    assert ack.phrase("Whaat do I have due next week?") in ack._AGENDA[0]
    assert ack.phrase("what's on tomorrow?") in ack._AGENDA[0]
    assert ack.phrase("anything I need to do this weekend?") in ack._AGENDA[0]


def test_a_day_in_small_talk_is_not_an_agenda():
    assert ack.phrase("how was your day today?") is None
    assert ack.phrase("what are you up to this weekend") is None


def test_is_question():
    assert commands.is_question("whaat do I have due?")
    assert commands.is_question("hey nova, what's the time")
    assert commands.is_question("is there anything on tonight")
    assert not commands.is_question("can you set an alarm")
    assert not commands.is_question("remind me to stretch")


def test_mock_mode_never_asks_a_model(monkeypatch):
    monkeypatch.setattr(ack, "MOCK_LLM", True)

    def boom(*a, **k):
        raise AssertionError("asked the model")

    monkeypatch.setattr(llm, "parse", boom)
    assert ack.phrase("what's on tomorrow at the meeting") is not None


def test_the_model_decides_even_without_a_second_gpu(monkeypatch):
    monkeypatch.setattr(llm, "SEPARATE_BACKGROUND", False)
    seen = {}

    def parse(system, user, model_cls, **kwargs):
        seen.update(kwargs)
        return model_cls(ack="Let me check your power systems test results.")

    monkeypatch.setattr(llm, "parse", parse)
    assert ack.phrase("How'd I go on the first written testt for power systems?") ==         "Let me check your power systems test results."
    # It's part of answering this turn, so it must not wait for the turn.
    assert seen["wait_for_voice"] is False


def test_background_model_phrases_it(monkeypatch):
    monkeypatch.setattr(llm, "SEPARATE_BACKGROUND", True)
    seen = {}

    def parse(system, user, model_cls, **kwargs):
        seen.update(kwargs)
        return model_cls(ack="Sure, checking Thursday now.")

    monkeypatch.setattr(llm, "parse", parse)
    assert ack.phrase("am I free Thursday") == "Sure, checking Thursday now."
    assert seen["timeout"] == ack.MODEL_TIMEOUT_S and seen["max_retries"] == 0


def test_the_model_can_decide_nothing_needs_saying(monkeypatch):
    monkeypatch.setattr(llm, "SEPARATE_BACKGROUND", True)
    monkeypatch.setattr(llm, "parse", lambda s, u, cls, **k: cls(ack=None))
    # Even though "free" is a calendar word.
    assert ack.phrase("what do you do in your free time") is None


def test_a_slow_or_rambling_model_falls_back_to_keywords(monkeypatch):
    monkeypatch.setattr(llm, "SEPARATE_BACKGROUND", True)

    def slow(*a, **k):
        raise TimeoutError

    monkeypatch.setattr(llm, "parse", slow)
    assert ack.phrase("what's on my calendar") in ack._TOPICS["calendar"][1]

    monkeypatch.setattr(llm, "parse", lambda s, u, cls, **k: cls(ack="x" * 200))
    assert ack.phrase("what's on my calendar") in ack._TOPICS["calendar"][1]


def test_the_route_is_registered_and_signed_in():
    from app.main import app

    assert "post" in app.openapi()["paths"]["/event/ack"]
    from app.core.auth import PUBLIC_ROUTES
    assert "/event/ack" not in PUBLIC_ROUTES


def test_waking_up_is_said_for_every_turn(monkeypatch):
    monkeypatch.setattr(llm, "prompt_cached", False)
    assert ack.phrase("How are you?") in ack._WAKING
    assert ack.phrase("what's on my calendar") in ack._WAKING
    assert ack.phrase("") is None


def test_once_warm_the_usual_rules_apply(monkeypatch):
    monkeypatch.setattr(llm, "prompt_cached", True)
    assert ack.phrase("How are you?") is None


def test_standing_instructions_reach_the_model(monkeypatch):
    monkeypatch.setattr(ack, "_standing_instructions", lambda user_id: ["Always talk like a pirate."])
    seen = {}

    def parse(system, user, model_cls, **kwargs):
        seen["system"] = system
        return model_cls(ack="Arr, let me check yer marks!")

    monkeypatch.setattr(llm, "parse", parse)
    assert ack.phrase("how did I go on the test?", user_id="u1") == "Arr, let me check yer marks!"
    assert "Always talk like a pirate." in seen["system"]


def test_with_standing_instructions_no_fixed_line(monkeypatch):
    # The model is unreachable (the fixture): a plain-English keyword or
    # waking line would break the user's style, so nothing is said.
    monkeypatch.setattr(ack, "_standing_instructions", lambda user_id: ["Always talk like a pirate."])
    assert ack.phrase("what's on my calendar?", user_id="u1") is None
    monkeypatch.setattr(llm, "prompt_cached", False)
    assert ack.phrase("how are you", user_id="u1") is None


def test_waking_asks_the_model_for_the_line(monkeypatch):
    monkeypatch.setattr(llm, "prompt_cached", False)
    seen = {}

    def parse(system, user, model_cls, **kwargs):
        seen["system"] = system
        return model_cls(ack="One moment, I'm just waking up.")

    monkeypatch.setattr(llm, "parse", parse)
    assert ack.phrase("How are you?") == "One moment, I'm just waking up."
    assert ack.WAKING_PROMPT in seen["system"]
