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
          |                             |
          v                             v
    run immediately          check gain + confidence
                                        |
                                  high enough?
                                  /           \
                                yes           no
                                |              |
                                v              v
                            Seek user        Ignore
                            accept/reject                            
                           /           \
                        accept       reject    
                            |          |
                            v          v
                            run      ignore
                            
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