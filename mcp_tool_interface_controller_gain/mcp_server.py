"""
Minimal starter MCP server - for learning the mechanics before building
Nova's real Section 5.7 (Tool / MCP interface + Controller Gain).

WHAT THIS IS
An MCP server is just a small program that:
  1. Advertises a list of "tools" (functions) it can perform, each with a
     name, a description, and an input_schema (what arguments it takes).
  2. Waits for a "client" (in Nova's case, the Intent Surface / Claude's
     tool-calling loop) to ask "what tools do you have?" and then to call
     one with some arguments.
  3. Runs the function and returns a result.

That's the whole protocol at the beginner level. Everything else (gain,
reinforcement, thresholds) is Nova-specific logic bolted on top.


HOW TO RUN
    pip install fastmcp
    download this: https://nodejs.org/en/download
    python -m venv venv
    source venv/Scripts/activate
    fastmcp dev inspector mcp_server.py
"""

from fastmcp import FastMCP

mcp = FastMCP("nova-demo-server")

# ---------------------------------------------------------------------------
# Controller Gain scaffolding
#
# MCP itself has NO concept of "gain" - it's a convention Nova adds on top
# of the standard {name, description, input_schema} tool contract. Start it
# as a plain dict so you can reason about the logic in isolation. Later this
# becomes a small state store (a DB row per tool) that Memory outcomes can
# update.
# ---------------------------------------------------------------------------

TOOL_GAIN = {
    "check_weather": 0.1,  # starts low -> reactive-only by default
}

GAIN_THRESHOLD = 0.5


def clears_proactive_threshold(tool_name: str, state_confidence: float) -> bool:
    """
    Gate for PROACTIVE firing only. Reactive calls (explicit user request)
    always bypass this check - gain never blocks an explicit ask.

    gain * state_confidence >= threshold  ->  allowed to fire proactively
    """
    gain = TOOL_GAIN.get(tool_name, 0.0)
    return (gain * state_confidence) >= GAIN_THRESHOLD


def reinforce(tool_name: str, accepted: bool, step: float = 0.1) -> None:
    """
    Toy reinforcement rule: accepted proactive action -> gain up,
    rejected/undone -> gain down. Clamped to [0, 1].
    The real version reads this signal from the Memory store (Section 5.5),
    not a function argument.
    """
    current = TOOL_GAIN.get(tool_name, 0.0)
    delta = step if accepted else -step
    TOOL_GAIN[tool_name] = max(0.0, min(1.0, current + delta))


# ---------------------------------------------------------------------------
# The tool itself - stand-in for a real Nova function (notes/calendar/etc.)
# ---------------------------------------------------------------------------

@mcp.tool
def check_weather(city: str) -> str:
    """Look up the current weather for a city. Placeholder tool for
    learning MCP mechanics - swap in a real API call later."""
    return f"It's sunny in {city} (fake data - replace with a real API)."


if __name__ == "__main__":
    mcp.run()