"""
Base class for NOVA tools.

NOVA has three main Function tools:
    - Function 1
    - Function 2
    - Function 3

Each Function should have its own class that inherits from BaseTool:
    Function1Tool(BaseTool)
    Function2Tool(BaseTool)
    Function3Tool(BaseTool)

BaseTool provides the common structure shared by all Function tools:
- a name
- a description
- an input schema
- an invoke() function to run the tool

NOTE: when you are writing a Function tool implement _execute(),
which contains the actual logic for that specific Function.

Example:

class NotesTool(BaseTool):
    def _execute(self, tool_input):
        # Notes-specific logic goes here
        return result

Gain handling is not part of BaseTool. Gains are added later by registry.py, which connects each Function
tool with its gain. This keeps normal callable tools separate from tools that are allowed to trigger proactively.
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema

    def invoke(self, tool_input: dict[str, Any]) -> Any:
        """
        Runs the tool.
        All tool calls go through invoke() which calls the tool's own _execute() function.
        """
        return self._execute(tool_input)

    @abstractmethod
    def _execute(self, tool_input: dict[str, Any]) -> Any:
        """
        TODO: Implement function here.
        """
        raise NotImplementedError