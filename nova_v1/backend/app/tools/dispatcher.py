"""
Runs NOVA Function tools.

The dispatcher is used to decide when a tool should run.

There are two ways a tool can be called:

1. Reactive call:
   The user directly asks for something.
   Example:
       "Create a calendar event"

   The tool always runs.
   Gain is ignored because the user requested it.

2. Proactive call:
   NOVA decides a tool might be useful.
   Example:
       "The user usually gets a reminder before meetings"

   The dispatcher checks:

       state_confidence * tool_gain >= threshold

   If the score is high enough, the tool runs.
   Otherwise, nothing happens.

Which of the two a given call is, is decided by the caller and passed to
should_run() as `trigger`. In V1 that comes from the Intent Surface: every
Function tool's schema carries a required `trigger` field the model fills in
("requested" when the user asked, "inferred" when the model is acting on its
own reading of the situation). See intent_surface/loop.py.


   



                    Intent Surface
                         |
                         |
                  "I want to use Notes"
                         |
                         v
                   Dispatcher
                         |
          +--------------+--------------+
          |                             |
     User asked                 NOVA suggested
    (trigger=            (trigger="inferred")
     "requested")                       |
          |                             v
          v                  check gain * confidence
    run immediately                     |
    (gain ignored)               high enough?
                                  /           \
                                yes           no
                                |              |
                                v              v
                               run          suppress

V1 SIMPLIFICATION: the idealised design asks the user to accept/reject a
proactive proposal before running it, because that accept/reject is the
reinforcement signal that retunes the gain. V1 has no reinforcement - gain is
purely user-set - so there is nothing to collect and the confirmation step is
friction with no payoff. A proposal that clears the threshold therefore runs
straight away. reinforcement.py stays in the tree for when that loop is built;
should_run() is the seam it would hook back into.

Only Function tools registered in ToolRegistry can be
dispatched here:
- Function 1
- Function 2
- Function 3

Context tools (such as searches or lookups) do not go
through the dispatcher because they do not have gain values
and should not trigger actions by themselves.


Does not log to Memory. Per base.py's docstring, outcome logging happens
at the call site (whoever calls dispatch_reactive/dispatch_proactive, 
likely Georgia), once Jay's memory module exists.
"""

from dataclasses import dataclass
from typing import Any

from .registry import ToolRegistry

try:
    from ..gain.config import FIRING_THRESHOLD, clamp
except ImportError:  # pragma: no cover
    from gain.config import FIRING_THRESHOLD, clamp


@dataclass(frozen=True)
class DispatchResult:
    """
    Outcome of a *proposal* check, not an execution. Proactive calls no
    longer run the tool here - they only decide whether it's worth
    asking the user. See confirm_proactive() for the actual run, and
    reinforcer.reinforce() for recording the user's accept/reject.
    """

    proposed: bool
    name: str
    effective_gain: float
    state_confidence: float


class Dispatcher:
    def __init__(self, registry: ToolRegistry) -> None:
        # the dispatcher uses the registry to find tools and their gains
        self.registry = registry

    def dispatch_reactive(self, name: str, tool_input: dict[str, Any]) -> Any:
        """
        Run a tool because the user asked for it.
        Raises KeyError (via the registry) if `name` isn't a registered Function tool.
        """
        self._require_registered(name)
        return self.registry.get_tool(name).invoke(tool_input)

    def dispatch_proactive(
        self,
        name: str,
        tool_input: dict[str, Any],
        state_confidence: float,
    ) -> DispatchResult:
        """
        Inferred need, no explicit request. Decides whether this is
        worth *proposing* to the user - it does NOT run the tool.

        proposed=True means state_confidence * effective_gain >=
        FIRING_THRESHOLD: surface the proposal to the user and wait for
        accept/reject before calling confirm_proactive().

        Raises KeyError (via the registry) if 'name' isn't a registered
        Function tool.
        """
        self._require_registered(name)

        # clamp between 0 and 1
        state_confidence = clamp(state_confidence)

        # how willing is this tool to act automatically?
        effective_gain = self.registry.get_gain(name).get_effective()

        return DispatchResult(
            proposed=state_confidence * effective_gain >= FIRING_THRESHOLD,
            name=name,
            effective_gain=effective_gain,
            state_confidence=state_confidence,
        )

    def should_run(
        self,
        name: str,
        trigger: str,
        state_confidence: float,
    ) -> DispatchResult:
        """
        The single question a caller actually needs answered: given how this
        call was triggered, is this tool allowed to run?

        - trigger "requested" -> always yes. The user asked; Section 5.7 says
          gain governs action on *inferred* intent only, so a reactive call
          ignores it entirely. effective_gain is still reported for logging.
        - trigger "inferred"  -> yes only if state_confidence * effective_gain
          clears FIRING_THRESHOLD.

        Deciding is separate from running because not every tool runs here:
        get_calendar_range executes on the phone (loop.py's CLIENT_TOOLS), and
        it still has to pass the same gate before the backend pauses the
        conversation and calls out to the device.

        Raises KeyError (via the registry) if 'name' isn't a registered
        Function tool.
        """
        self._require_registered(name)

        if trigger == "requested":
            return DispatchResult(
                proposed=True,
                name=name,
                effective_gain=self.registry.get_gain(name).get_effective(),
                state_confidence=clamp(state_confidence),
            )

        return self.dispatch_proactive(name, {}, state_confidence)

    def confirm_proactive(self, name: str, tool_input: dict[str, Any]) -> Any:
        """
        Actually run a proactive tool call - call this only after the
        user has accepted the proposal from dispatch_proactive(). No
        gain check here; that already happened when the proposal was
        made.

        Raises KeyError (via the registry) if 'name' isn't a registered
        Function tool.
        """
        self._require_registered(name)
        return self.registry.get_tool(name).invoke(tool_input)


    def _require_registered(self, name: str) -> None:
        """
        Make sure this is a real NOVA Function tool.

        Unknown tools should not be dispatched.
        """

        if not self.registry.has(name):
            raise KeyError(
                f"'{name}' is not a registered Function tool. "
            )