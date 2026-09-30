"""set_reminder's authority at each row of its gain table.

authority = gain x obligation_cue x prediction_confidence, acting at
FIRING_THRESHOLD (0.15). Built on a registry holding only set_reminder with an
explicit gain, so no gain store or Supabase is involved.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.control.commands import Command, classify
from app.control.controller import ProportionalController, Reason
from app.control.cues import STRONG, WEAK
from app.control.gain.config import FIRING_THRESHOLD
from app.control.gain.controller_gain import ControllerGain
from app.control.observer import Observation, observe
from app.schemas.event import TimeEvent, VoiceEvent
from app.schemas.user_state import UserState
from app.tools.core.registry import ToolRegistry
from app.tools.functions.reminder_tool import SetReminderTool


def decide(gain, cue, confidence, command=None):
    registry = ToolRegistry()
    registry.register(SetReminderTool(), ControllerGain(name="set_reminder", value=gain))
    turn = ProportionalController(registry).open_turn(
        Observation(obligation_cue=cue, prediction_confidence=confidence), command,
    )
    return turn.decision("set_reminder")


def test_threshold_is_what_the_table_assumes():
    assert FIRING_THRESHOLD == 0.15


# (gain, cue, confidence, authorised) - each row's boundary and
# just-below, for every row that has a boundary.
TABLE = [
    (0.0, STRONG, 1.0, False),     # zero gain is always silent
    (0.15, STRONG, 1.0, True),     # the seed: only a perfect estimate
    (0.15, STRONG, 0.8, False),    # ...so effectively reactive
    (0.15, WEAK, 1.0, False),
    (0.20, STRONG, 0.75, True),
    (0.20, STRONG, 0.70, False),
    (0.20, WEAK, 1.0, False),
    (0.30, STRONG, 0.5, True),
    (0.30, STRONG, 0.45, False),
    (0.30, WEAK, 1.0, True),
    (0.30, WEAK, 0.9, False),
    (0.60, STRONG, 0.25, True),
    (0.60, STRONG, 0.2, False),
    (0.60, WEAK, 0.5, True),
    (0.60, WEAK, 0.45, False),
    (1.0, STRONG, 0.15, True),
    (1.0, STRONG, 0.1, False),
    (1.0, WEAK, 0.3, True),
    (1.0, WEAK, 0.25, False),
]


@pytest.mark.parametrize("gain, cue, confidence, authorised", TABLE)
def test_authority_table(gain, cue, confidence, authorised):
    assert decide(gain, cue, confidence).authorised is authorised


def test_no_cue_is_no_divergence_not_open_loop():
    decision = decide(1.0, 0.0, 1.0)
    assert decision.reason is Reason.NO_DIVERGENCE


def test_zero_confidence_falls_back_to_reactive():
    decision = decide(1.0, STRONG, 0.0)
    assert not decision.authorised
    assert decision.reason is Reason.NO_PREDICTION


def test_remind_me_runs_at_zero_gain():
    decision = decide(0.0, 0.0, 0.0, command=Command(text="remind me to call mum"))
    assert decision.authorised and decision.reason is Reason.COMMANDED


# End to end through observe(): the Definition of Done's sentence, with a
# typical 0.8 prediction confidence (calendar + activity + location).
def typical_state():
    return UserState(calendar_ctx="free", activity="still", location_ctx="-35.28,149.13",
                     utc_offset_minutes=600)


def end_to_end(text, gain):
    event = VoiceEvent(id=uuid4(), timestamp=datetime(2026, 9, 23, 2, 0, tzinfo=timezone.utc),
                       text=text)
    observation = observe(event, typical_state())
    assert observation.prediction_confidence == pytest.approx(0.8)
    registry = ToolRegistry()
    registry.register(SetReminderTool(), ControllerGain(name="set_reminder", value=gain))
    turn = ProportionalController(registry).open_turn(observation, classify(event))
    return turn.decision("set_reminder")


def test_stated_deadline_sets_a_reminder_at_gain_03():
    assert end_to_end("I need to hand in the form by 5", 0.3).authorised


def test_stated_deadline_does_not_at_the_seed():
    decision = end_to_end("I need to hand in the form by 5", 0.15)
    assert not decision.authorised
    assert decision.reason is Reason.BELOW_THRESHOLD


def test_ambient_tick_is_silent_at_full_gain():
    event = TimeEvent(id=uuid4(), timestamp=datetime(2026, 9, 23, 2, 0, tzinfo=timezone.utc))
    observation = observe(event, typical_state())
    assert observation.obligation_cue == 0.0
    registry = ToolRegistry()
    registry.register(SetReminderTool(), ControllerGain(name="set_reminder", value=1.0))
    decision = ProportionalController(registry).open_turn(observation, None).decision("set_reminder")
    assert not decision.authorised
