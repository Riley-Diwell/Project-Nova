"""
Registry for Nova's Function tools.

The registry stores the three main Nova Functions:
    - Function 1
    - Function 2
    - Function 3

Each Function tool is registered with its controller gain.

BaseTool only contains the tool itself:
- name
- description
- input schema
- execute logic

The registry adds the gain because only Function tools should be able
to trigger proactively (not contextual tools).

The registry is used by:
- Intent Surface: get the list of available tools
- Dispatcher: check a tool's gain before proactive calls
- Reinforcement: update a tool's gain after outcomes

Context tools (such as lookups or information gathering tools) should
NOT be registered here because they do not have gains and should not
trigger proactively.

Example:

tool = NotesTool()
registry.register(tool)

Now the registry knows:
- how to run the tool
- what the tool looks like
- how likely it is to fire proactively
"""

from dataclasses import dataclass
from typing import Optional

from .base import BaseTool
from .schema import ToolSchema

try:
    from ..gain.controller_gain import ControllerGain
    from ..gain.gain_store import GainStore
except ImportError:  # pragma: no cover
    from gain.controller_gain import ControllerGain
    from gain.gain_store import GainStore


@dataclass
class _Entry:
    "Store a tool and its gain together."
    tool: BaseTool
    gain: ControllerGain


class ToolRegistry:
    def __init__(self, gain_store: Optional[GainStore] = None) -> None:
        self._entries: dict[str, _Entry] = {}

        # optional - if set, register() loads saved gains from here
        self.gain_store = gain_store

    def register(self, tool: BaseTool, gain: Optional[ControllerGain] = None) -> None:
        """
        Add a Function tool to the registry.

        Gain resolution order:
        1. explicit `gain` argument, if given
        2. saved gain from `self.gain_store`, if a store is attached and
           has an entry for this tool
        3. a fresh default ControllerGain
        """
        if tool.name in self._entries:
            raise ValueError(f"tool '{tool.name}' is already registered")

        if gain is None and self.gain_store is not None:
            gain = self.gain_store.load(tool.name)

        self._entries[tool.name] = _Entry(
            tool=tool,
            gain=gain or ControllerGain(name=tool.name),
        )

    def has(self, name: str) -> bool:
        """
        Check if a tool is a registered Function tool.

        Used to tell the difference between:
        - Function tools (have gain)
        - Context tools (no gain)
        """
        return name in self._entries

    def get_tool(self, name: str) -> BaseTool:
        """
        Get the actual tool so it can be run.

        The caller uses:
            tool.invoke(input)
        """
        return self._entries[name].tool

    def get_gain(self, name: str) -> ControllerGain:
        """
        Get the tool's gain.

        Used by dispatcher.py to decide whether
        a proactive call should happen.
        """
        return self._entries[name].gain

    def get_schema(self, name: str) -> ToolSchema:
        """
        Create the tool description that is sent to the model.

        Includes:
        - name
        - description
        - input format
        - current gain
        """
        entry = self._entries[name]
        return ToolSchema(
            name=entry.tool.name,
            description=entry.tool.description,
            input_schema=entry.tool.input_schema,
            gain=entry.gain.get_effective(),
        )

    def get_schemas(self) -> list[ToolSchema]:
        """
        Get all registered Function tool descriptions.

        Used when building the tool list for the model.
        """
        return [self.get_schema(name) for name in self._entries]

    def all_names(self) -> list[str]:
        """
        Return the names of all registered tools.
        """
        return list(self._entries)
