"""
tools/example/demo_tool.py — a trivial reference tool, not a real function.

Exists purely to prove tools/base.py works end to end before any of the
three real function MCP servers exist (Phase 1's milestone: "Intent
Surface with one trivial tool"). Also what test_gain_flow.py exercises
against, so the tests aren't blocked on notification_batcher / notes /
calendar being finished.

Not a template to copy for real tools — just echoes its input back.
Function owners subclassing BaseTool for a real tool should look at
base.py's docstring, not this file, for what's required.
"""

from typing import Any

from ..base import BaseTool


class DemoTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="demo_echo",
            description="Echoes whatever input it's given. Reference tool only — not a real function.",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        return {"echoed": tool_input}
