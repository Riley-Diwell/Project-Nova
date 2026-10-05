"""commands.classify - was this a command? Known readings pinned as tests.

Only the reminder-related frames are covered here so far; the rest of the
classifier predates this file.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.control.commands import classify
from app.schemas.event import VoiceEvent


def voice(text):
    return VoiceEvent(id=uuid4(), timestamp=datetime.now(timezone.utc), text=text)


@pytest.mark.parametrize("text", [
    "remind me to call mum at 5",
    "hey nova, please remind me in 20 minutes to take the pasta off",
    "at 5 remind me to call mum",
    "tomorrow morning remind me to bring my laptop",
    "don't let me forget to submit the form",
    "dont let me forget the milk",
    "snooze that for an hour",
    "what reminders do I have next week",
])
def test_reminder_asks_are_commands(text):
    assert classify(voice(text)) is not None


@pytest.mark.parametrize("text", [
    "write me a note with three tips for my presentation",
    "write a list of things to pack",
    "jot down that the lab moved to room 4",
    "draft me a summary of today's lecture",
])
def test_note_writing_asks_are_commands(text):
    # Not a command, the memory tool isn't offered and the model says it
    # can't save notes.
    assert classify(voice(text)) is not None


@pytest.mark.parametrize("text", [
    "I need to email him",
    "I have to submit the form by 5",
    "coffee with Sam on Thursday at ten",
])
def test_stated_obligations_are_not_commands(text):
    # These are what set_reminder's gain governs - see test_cues.py.
    assert classify(voice(text)) is None
