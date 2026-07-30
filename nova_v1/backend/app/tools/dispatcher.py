"""
Runs NOVA Function tools.

The one place a Function tool gets invoked by name. That is all it is now.


                    Intent Surface
                          |
                          | Claude called navigation_departure_time
                          v
                    Controller  ── already decided, before the model ran
                          |
                          v
                     Dispatcher  ── invoke it
                          |
                          v
                       the tool


Does not log to Memory. The Action is recorded at the call site, which is where
the Controller's decision is also in scope - so the Action carries the control
trace that produced it.
"""

from typing import Any

from .registry import ToolRegistry


class Dispatcher:
    def __init__(self, registry: ToolRegistry) -> None:
        # the dispatcher uses the registry to find tools
        self.registry = registry

    def dispatch_reactive(self, name: str, tool_input: dict[str, Any]) -> Any:
        """
        Run a Function tool.

        Named `reactive` from when this module also had a proactive path. It no
        longer does: by the time a call reaches here the Controller has
        authorised it, and *why* it was authorised - a command, or measured
        divergence - changes what gets recorded on the Action, not how the tool is
        run.

        Raises KeyError (via the registry) if `name` isn't a registered Function
        tool.
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
