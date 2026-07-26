"""
Demo to show how Function tools should be written.
"""

from typing import Any

from ..base import BaseTool


class DemoTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="demo_echo",
            description="Echoes whatever input it's given. Reference tool only - not a real function.",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        return {"echoed": tool_input}
