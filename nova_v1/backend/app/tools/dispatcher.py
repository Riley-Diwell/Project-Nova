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
                                  /       \
                                yes       no
                                |          |
                                v          v
                            Run tool    Ignore

                            

                            
Only Function tools registered in ToolRegistry can be
dispatched here:
- Function 1
- Function 2
- Function 3

Context tools (such as searches or lookups) do not go
through the dispatcher because they do not have gain values
and should not trigger actions by themselves.


Does not log to Memory. Per base.py's docstring, outcome logging happens
at the call site (whoever calls dispatch_reactive/dispatch_proactive),
once Jay's memory module exists — not hardcoded in here.
"""

from dataclasses import dataclass
from typing import Any, Optional

from .registry import ToolRegistry
from ..gain.config import FIRING_THRESHOLD, clamp


@dataclass(frozen=True)
class DispatchResult:
    """
    What happened when a proactive dispatch was attempted. Reactive calls
    don't need this - they always fire, so they just return the tool's
    raw output. This exists so a caller (and eventually a Memory logger)
    can tell "blocked by gain" apart from "the tool returned None."
    """

    fired: bool
    name: str
    effective_gain: float
    state_confidence: float
    output: Optional[Any] = None


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
        Inferred need, no explicit request. Fires only if
        state_confidence * effective_gain >= FIRING_THRESHOLD.
        Raises KeyError (via the registry) if `name` isn't a registered
        Function tool - a context/inference tool has no business being
        passed here at all (see module docstring).
        """
        self._require_registered(name)

        # clamp between 0 and 1
        state_confidence = clamp(state_confidence)

        # how willing is this tool to act automatically?
        effective_gain = self.registry.get_gain(name).get_effective()

        # confidence is not high enough, do not run the tool
        if state_confidence * effective_gain < FIRING_THRESHOLD:
            return DispatchResult(
                fired=False,
                name=name,
                effective_gain=effective_gain,
                state_confidence=state_confidence,
            )

        # confidence was high enough, run the tool
        output = self.registry.get_tool(name).invoke(tool_input)
        return DispatchResult(
            fired=True,
            name=name,
            effective_gain=effective_gain,
            state_confidence=state_confidence,
            output=output,
        )

    def _require_registered(self, name: str) -> None:
        """
        Make sure this is a real NOVA Function tool.

        Unknown tools should not be dispatched.
        """

        if not self.registry.has(name):
            raise KeyError(
                f"'{name}' is not a registered Function tool. "
            )