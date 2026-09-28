"""control/cues.obligation_cue - known readings, including known misreadings.

A misreading is pinned here as a test rather than left as folklore, the same
convention commands.py sets. If one gets fixed, move it to the right table.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.control.cues import STRONG, WEAK, obligation_cue
from app.schemas.event import VoiceEvent


def voice(text):
    return VoiceEvent(id=uuid4(), timestamp=datetime.now(timezone.utc), text=text)


STRONG_CUES = [
    "I need to submit the form by 5",
    "I have to email Dr Chen tomorrow",
    "I've got to call mum tonight",
    "i gotta hand this in by five",
    "I must pay rent on Friday",
    "I should book the dentist this arvo",
    "I'm supposed to meet Sam at 3pm",
    "can't forget to take my meds after lunch",
    "remember to grab milk after class",
    "hey nova, I need to finish the essay by midnight",
    "I need to move the car in 20 minutes",
    "I have to reply to that email before the lecture",
    "I need to check the oven in half an hour",
    "I need to renew my licence next week",
]

WEAK_CUES = [
    "I need to email him",
    "I have to call mum",
    "I should really drink more water",
    "I'm supposed to be studying",
    "I must remember my keys",
]

NO_CUE = [
    "what's the weather",
    "coffee with Sam on Thursday at ten",
    "I don't need to go today",
    "I needed to leave early yesterday",
    "that lecture was long",
    "I shouldn't have eaten that",
    "",
]

# Known misreadings. Each is pinned to what the rule does, not what it should.
MISREAD_AS_OBLIGATION = [
    ("I have to say, that was good", WEAK),          # a figure of speech
    ("I should be fine tomorrow", STRONG),            # not an obligation at all
]
MISSED_OBLIGATIONS = [
    "the assignment is due at 5",                      # an obligation, not in the first person
    "gotta submit by five",                            # no subject
]


@pytest.mark.parametrize("text", STRONG_CUES)
def test_obligation_with_time_is_strong(text):
    assert obligation_cue(voice(text)) == STRONG


@pytest.mark.parametrize("text", WEAK_CUES)
def test_obligation_alone_is_weak(text):
    assert obligation_cue(voice(text)) == WEAK


@pytest.mark.parametrize("text", NO_CUE)
def test_no_obligation(text):
    assert obligation_cue(voice(text)) == 0.0


@pytest.mark.parametrize("text, score", MISREAD_AS_OBLIGATION)
def test_known_misreadings(text, score):
    assert obligation_cue(voice(text)) == score


@pytest.mark.parametrize("text", MISSED_OBLIGATIONS)
def test_known_misses(text):
    assert obligation_cue(voice(text)) == 0.0


def test_non_voice_events_score_zero():
    tick = SimpleNamespace(type="timestamp", timestamp=datetime.now(timezone.utc))
    assert obligation_cue(tick) == 0.0
    note = SimpleNamespace(type="notification", text="I need to submit by 5")
    assert obligation_cue(note) == 0.0
