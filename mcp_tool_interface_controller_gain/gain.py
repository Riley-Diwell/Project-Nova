"""
nova_gain.py - Section 5.7: Tool / MCP interface + Controller Gain (Naoise)

WHAT THIS FILE IS
Not a function. Not a demo tool. This is the shared platform piece every
function's MCP server (notification management, notes, calendar, ...)
builds on top of. It does two jobs:

  1. Defines the standard tool contract: every tool a function exposes is
     registered with {name, description, input_schema, gain} - MCP itself
     only gives you the first three, so `gain` is a Nova convention this
     file adds via a decorator + a store.

  2. Implements the Controller Gain mechanism: per-tool gain that is
     self-tuning (reinforcement from Memory outcomes moves it), user-
     overridable (clamps it), and gates PROACTIVE firing only - reactive
     calls (explicit user request) always go through.

WHO USES THIS
  - Function owners (Ella, calendar owner, notes owner) import `nova_tool`
    to register each tool with an initial gain, alongside their normal
    `@mcp.tool` decorator.
  - The Intent Surface (Georgia) imports `clears_proactive_threshold`
    and calls it before executing any tool call Claude proposes
    proactively. Reactive calls skip this check entirely.
  - The Memory store (Jay) calls `reinforce()` after logging an outcome
    (user accepted a proactive action -> True, rejected/undid -> False).
  - The Knowledge Map UI (Jay/Riley) calls `override()` when the user
    manually drags a tool's gain slider.

STORAGE
  V1 prototype: a JSON file on disk (GainStore below). This is deliberately
  swappable - same three methods (get / set / all) would back onto a
  Supabase table in V2 with no change to the interface above it. That's
  the same "swap the backing implementation, not the seam" pattern the
  rest of NOVA uses (see DESIGN.md Section 10).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GAIN_THRESHOLD = 0.5          # gain * state_confidence must clear this to fire proactively
REINFORCE_STEP = 0.1          # how much a single accept/reject moves gain
GAIN_MIN, GAIN_MAX = 0.0, 1.0
DEFAULT_STORE_PATH = Path(os.environ.get("NOVA_GAIN_STORE", "tool_gain.json"))


# ---------------------------------------------------------------------------
# Per-tool gain record
# ---------------------------------------------------------------------------

@dataclass
class GainRecord:
    tool_name: str
    gain: float
    locked: bool = False          # True once a user has manually overridden it
    updated_at: float = field(default_factory=time.time)
    updated_by: str = "default"   # "default" | "reinforcement" | "user"


# ---------------------------------------------------------------------------
# GainStore - the only piece that changes when this moves to Supabase.
# Swap this class for a SupabaseGainStore with the same three methods and
# nothing above it needs to change.
# ---------------------------------------------------------------------------

class GainStore:
    def __init__(self, path: Path = DEFAULT_STORE_PATH):
        self._path = path
        self._data: dict[str, GainRecord] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            raw = json.loads(self._path.read_text())
            self._data = {k: GainRecord(**v) for k, v in raw.items()}

    def _save(self) -> None:
        self._path.write_text(
            json.dumps({k: asdict(v) for k, v in self._data.items()}, indent=2)
        )

    def register(self, tool_name: str, initial_gain: float) -> None:
        """Called once, typically at import time via @nova_tool. No-op if
        the tool already has a record (don't clobber learned/user gain on
        every server restart)."""
        if tool_name not in self._data:
            self._data[tool_name] = GainRecord(tool_name, initial_gain)
            self._save()

    def get(self, tool_name: str) -> float:
        record = self._data.get(tool_name)
        return record.gain if record else 0.0

    def all(self) -> dict[str, GainRecord]:
        return dict(self._data)

    def set(self, tool_name: str, value: float, source: str) -> None:
        value = max(GAIN_MIN, min(GAIN_MAX, value))
        existing = self._data.get(tool_name)
        locked = existing.locked if existing else False
        self._data[tool_name] = GainRecord(
            tool_name, value, locked=locked, updated_by=source
        )
        self._save()

    def lock(self, tool_name: str, locked: bool) -> None:
        if tool_name in self._data:
            self._data[tool_name].locked = locked
            self._save()


store = GainStore()


# ---------------------------------------------------------------------------
# The standard tool contract - decorator function owners use
# ---------------------------------------------------------------------------

def nova_tool(name: str, initial_gain: float = 0.1) -> Callable:
    """
    Stack this under @mcp.tool in a function's MCP server:

        @mcp.tool
        @nova_tool(name="notify_leave_time", initial_gain=0.1)
        async def notify_leave_time(message: str) -> str:
            ...

    Registers the tool's starting gain the first time the server boots.
    Doesn't touch the function's behaviour - MCP still sees a normal
    {name, description, input_schema} tool. Gain lives alongside it in the
    store, not inside the schema Claude reads.
    """
    def decorator(fn: Callable) -> Callable:
        store.register(name, initial_gain)
        fn._nova_tool_name = name
        return fn
    return decorator


# ---------------------------------------------------------------------------
# The gate - Intent Surface calls this before any PROACTIVE tool call
# ---------------------------------------------------------------------------

def clears_proactive_threshold(tool_name: str, state_confidence: float) -> bool:
    """
    gain * state_confidence >= GAIN_THRESHOLD -> allowed to fire proactively.

    Reactive calls (the user explicitly asked) never call this - gain and
    state confidence only gate unprompted action. This is the one function
    Georgia's Intent Surface loop needs to import from this file.
    """
    gain = store.get(tool_name)
    return (gain * state_confidence) >= GAIN_THRESHOLD


# ---------------------------------------------------------------------------
# Reinforcement - Memory calls this after logging an outcome
# ---------------------------------------------------------------------------

def reinforce(tool_name: str, accepted: bool, step: float = REINFORCE_STEP) -> float:
    """
    Nudge gain up (accepted) or down (rejected/undone) after a proactive
    firing. No-ops if the user has locked the gain via override() - a
    manual clamp always wins over the learned signal until the user
    changes it again.
    Returns the resulting gain.
    """
    record = store.all().get(tool_name)
    if record is None:
        store.register(tool_name, 0.1)
        record = store.all()[tool_name]

    if record.locked:
        return record.gain  # user override clamps reinforcement

    delta = step if accepted else -step
    new_gain = record.gain + delta
    store.set(tool_name, new_gain, source="reinforcement")
    return store.get(tool_name)


# ---------------------------------------------------------------------------
# User override - Knowledge Map UI calls this
# ---------------------------------------------------------------------------

def override(tool_name: str, value: float) -> float:
    """
    User manually sets a tool's gain (e.g. dragging a slider in the
    Knowledge Map). Locks it so future reinforcement doesn't quietly
    override the user's explicit choice.
    """
    store.set(tool_name, value, source="user")
    store.lock(tool_name, True)
    return store.get(tool_name)


def unlock(tool_name: str) -> None:
    """Let reinforcement resume moving this tool's gain again."""
    store.lock(tool_name, False)


# ---------------------------------------------------------------------------
# Demo / smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    TOOL = "notify_leave_time"
    store.register(TOOL, 0.1)

    print("initial gain:", store.get(TOOL))
    print("fires at confidence 0.9?", clears_proactive_threshold(TOOL, 0.9))  # False, 0.1*0.9 < 0.5

    # simulate the user accepting a few proactive "leave now" notifications
    for _ in range(5):
        reinforce(TOOL, accepted=True)
    print("gain after 5 accepts:", store.get(TOOL))
    print("fires at confidence 0.9 now?", clears_proactive_threshold(TOOL, 0.9))

    # user manually clamps it back down
    override(TOOL, 0.2)
    reinforce(TOOL, accepted=True)  # should be ignored - locked
    print("gain after user clamp + attempted reinforce:", store.get(TOOL))