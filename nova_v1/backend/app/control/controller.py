"""control/controller.py - the one place that decides whether NOVA acts.

WHAT THIS FILE IS
The Controller. It takes an Observation and a
classified Command and returns, per Function Tool, how much **authority** that
Tool has to act unasked this turn. The Intent Surface is then handed only the
Tools that have any, and its job narrows to parameterising them and phrasing the
reply.

WHY IT IS ONE MODULE
Because it was four. The decision to act used to be spread across a threshold
check in gain/, a per-Tool number in the registry, a `trigger` field the model
filled in about its own call, and about a hundred lines of system prompt written
to talk the model out of the failure modes that structure allowed. When NOVA
acted wrongly there was no way to say whether the fault was the gain, the state
estimate, the model's reading of a sentence, or the prompt. Story 17: one module,
so there is one place to look.

AUTHORITY, NOT PERMISSION
The old gate answered yes or no. This returns a magnitude, because the product
claim is that proactivity scales with how far off the user is (story 3):

    authority(tool) = clamp(gain(tool) x error(tool, observation)
                            x prediction_confidence)
    actuate if authority >= FIRING_THRESHOLD

Three things about that line matter.

*Gain multiplies an error*, which is what makes it a gain. It used to multiply the
State Estimator's confidence in its own current estimate - an estimator-quality
scalar - which answers "how sure are we, and how keen is this Tool" and never
"how far off are we". The consequence was visible: a Tool at high gain would
volunteer a calendar summary at a moment of no consequence, because nothing in
the product measured consequence.

*Prediction confidence de-rates control effort.* The loop does not act hard on a
poor estimate, and at zero confidence it contributes nothing at all - the system
falls back to reactive behaviour rather than guessing.

*A command is a reference step, not a disturbance correction.* The setpoint moved
because the user moved it, so there is no error to correct and gain has no
business in it. That is DESIGN.md Section 5.7's existing rule - gain governs
inferred intent only - given a control-theoretic reason rather than an assertion.

WHAT IS DELIBERATELY NOT HERE
Which Tool a sentence names. That is irreducibly linguistic and stays with the
model. This module knows a command was issued; it never knows what for.

The control law is one named implementation behind an abstract Controller, so a
richer law - integral term, per-Tool thresholds, anything - is a swap here and
nowhere else (story 21). That is the whole reason the seam exists.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

try:                                                   # app.control.controller
    from ..gain.config import FIRING_THRESHOLD, clamp
    from ..tools.registry import ToolRegistry
    from .commands import Command
    from .observer import Observation
except ImportError:  # pragma: no cover                 # control.controller
    from gain.config import FIRING_THRESHOLD, clamp
    from tools.registry import ToolRegistry
    from control.commands import Command
    from control.observer import Observation


class Reason(str, Enum):
    """Why a Tool was or was not authorised this turn.

    Recorded on the Action and therefore in the Episode, because "NOVA said
    nothing" is the hardest failure to debug and the reason is the whole answer
    (stories 16, 24). Every branch of the decision has exactly one of these, so
    a log line can never be silent about why.
    """

    COMMANDED = "commanded"                    # the user asked; gain not consulted
    DIVERGED = "diverged"                      # authority cleared the threshold
    BELOW_THRESHOLD = "below_threshold"        # some divergence, not enough
    NO_DIVERGENCE = "no_divergence"            # error is zero; nothing is wrong
    ZERO_GAIN = "zero_gain"                    # the user silenced this Function
    OPEN_LOOP = "open_loop"                    # no error term; reactive only
    NO_PREDICTION = "no_prediction"            # the Observer had nothing to go on
    REFUSED_EARLIER = "refused_earlier"        # already refused this turn
    NOT_A_FUNCTION_TOOL = "not_a_function_tool"


@dataclass(frozen=True)
class Decision:
    """What the Controller decided about one Tool, and the arithmetic behind it.

    The trace fields are None where they were not consulted rather than zero:
    a commanded call has no error because the error path was bypassed, and an
    open-loop Tool has none because it cannot measure one. Zero would claim
    something was measured and found calm, which is a different fact.
    """

    tool: str
    authorised: bool
    reason: Reason
    error: Optional[float] = None
    authority: Optional[float] = None
    gain: Optional[float] = None

    def trace(self) -> dict[str, float]:
        """The control trace for this decision's Action, ready to splat in."""
        return {
            name: value
            for name, value in (("error", self.error),
                                ("authority", self.authority),
                                ("gain", self.gain))
            if value is not None
        }


class Controller(ABC):
    """The seam. One method: what may act this turn?"""

    @abstractmethod
    def open_turn(self, observation: Observation,
                  command: Optional[Command]) -> "Turn":
        """Decide, for every Function Tool, whether it may act this turn."""
        raise NotImplementedError


class Turn:
    """One turn's decisions, and the memory of what was refused during it.

    Separate from the Controller because a Controller is long-lived and a turn is
    not, and because the once-refused-always-refused rule needs somewhere to live
    that dies with the turn. It used to be a `suppressed` set threaded through the
    Intent Surface's TurnContext, which put a control rule in the module that was
    supposed to be doing the talking.

    The decisions are computed once, at open_turn, from one Observation. Nothing
    re-reads a dial mid-turn: a turn is one situation, and a Function that was
    refused for it stays refused even if the user moves a slider while NOVA is
    mid-sentence.
    """

    def __init__(self, decisions: dict[str, Decision]) -> None:
        self._decisions = decisions
        self._refused: set[str] = set()

    def authorised(self) -> list[str]:
        """The Tools the Intent Surface may be offered, in registry order.

        Story 22: a Tool with no authority is *not offered*, rather than offered
        and then refused with ninety words explaining why it must not be
        mentioned. A refusal the model cannot see is a refusal it cannot argue
        with, relabel, or leak to the user.
        """
        return [name for name, d in self._decisions.items() if d.authorised]

    def decision(self, tool: str) -> Decision:
        """What was decided about `tool`, without recording anything."""
        return self._decisions.get(tool) or Decision(
            tool=tool, authorised=False, reason=Reason.NOT_A_FUNCTION_TOOL,
        )

    def allow(self, tool: str) -> Decision:
        """Check `tool` at the moment of calling, and remember a refusal.

        The sticky half of the contract. A Tool refused once in a turn stays
        refused, so the model cannot get a second bite - which it used to be able
        to do by relabelling the same call "requested", and which the prompt had a
        paragraph asking it not to.

        Authorised calls are not sticky in the other direction: a Tool the
        Controller cleared may be called twice in a turn, because two calendar
        ranges or two notes are one legitimate turn's work.
        """
        if tool in self._refused:
            refused = self.decision(tool)
            return Decision(
                tool=tool, authorised=False, reason=Reason.REFUSED_EARLIER,
                error=refused.error, authority=refused.authority, gain=refused.gain,
            )

        decided = self.decision(tool)
        if not decided.authorised:
            self._refused.add(tool)
        return decided


class ProportionalController(Controller):
    """Proportional control with a confidence de-rate - the first control law.

    Deliberately the simplest thing that is actually a controller: one
    proportional term, de-rated by how much the prediction is worth. No integral
    term, no derivative, no windup handling. Those are later swaps behind this
    same seam, and the seam exists so that swapping them is a local change.

    The one piece of integral behaviour NOVA has is Controller Gain itself, which
    reinforcement moves by a fixed step on the turn's Outcome (ADR-0002). That
    lives in gain/, not here: this module reads a gain and never writes one, so
    the decision stays a pure function of its inputs.
    """

    def __init__(self, registry: ToolRegistry,
                 threshold: float = FIRING_THRESHOLD) -> None:
        self.registry = registry
        self.threshold = threshold

    def open_turn(self, observation: Observation,
                  command: Optional[Command] = None) -> Turn:
        return Turn({
            name: self._decide(name, observation, command)
            for name in self.registry.all_names()
        })

    def _decide(self, tool: str, observation: Observation,
                command: Optional[Command]) -> Decision:
        """One Tool's authority this turn.

        The order of the branches is the contract, and it is worth reading as
        one:

          a command runs, whatever the dials say                     (story 4)
          a dial at zero never acts unasked, whatever the world says  (story 8)
          a Tool with no error term is reactive and says so          (story 27)
          zero divergence is silence                                 (story 2)
          otherwise, authority decides, in proportion                (story 3)
        """
        if command is not None:
            # A reference step. The user moved the setpoint, so there is no error
            # to correct and gain is not consulted - which is why no trace is
            # recorded: there was no arithmetic behind this.
            return Decision(tool=tool, authorised=True, reason=Reason.COMMANDED)

        gain = self.registry.get_gain(tool).get_effective()
        if gain <= 0.0:
            # The one assertion the Agency pillar reduces to. Checked before the
            # error term so a silenced Function is silenced even when the Tool's
            # own arithmetic would have shouted.
            return Decision(tool=tool, authorised=False,
                            reason=Reason.ZERO_GAIN, gain=gain)

        error = self.registry.get_tool(tool).error(observation)
        if error is None:
            return Decision(tool=tool, authorised=False,
                            reason=Reason.OPEN_LOOP, gain=gain)

        error = clamp(error)
        if error <= 0.0:
            return Decision(tool=tool, authorised=False, reason=Reason.NO_DIVERGENCE,
                            error=error, authority=0.0, gain=gain)

        confidence = clamp(observation.prediction_confidence)
        authority = clamp(gain * error * confidence)

        if authority >= self.threshold:
            return Decision(tool=tool, authorised=True, reason=Reason.DIVERGED,
                            error=error, authority=authority, gain=gain)

        # Distinguished so a log answers "why was NOVA quiet?" precisely: a poor
        # estimate and an unpersuasive one are different problems with different
        # fixes, and both look like silence from outside.
        reason = Reason.NO_PREDICTION if confidence <= 0.0 else Reason.BELOW_THRESHOLD
        return Decision(tool=tool, authorised=False, reason=reason,
                        error=error, authority=authority, gain=gain)
