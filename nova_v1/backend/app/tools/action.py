"""tools/action.py - one Tool call NOVA made (CONTEXT.md "Action").

WHAT THIS FILE IS
The single definition of the Action shape. It was a dict assembled inline in
intent_surface/loop.py, described in DESIGN.md Section 6 as a frozen seam and
checked nowhere. It is a type now for one concrete reason: the Controller's
output is what mints an Action, so the control trace (the error, the authority
and the gain behind the decision) has to be attached at the moment the decision
is made, and a dict with six optional keys assembled in three different places
is how fields go missing.

ONE SHAPE, THREE READERS
The same object is:
  - EventOut.actions, which the phone executes and displays,
  - the episode's `action` column, which Consolidation counts, and
  - the control log a changed control law can be replayed against.
There is no second queue and no parallel list of parameters. An earlier split -
tool *names* on the wire, parameters alongside - is how the wire came to carry
values the client could not parse.

THE WIRE IS FROZEN, THE TRACE IS ADDITIVE
`{tool, input, trigger, ran}` is what Android's NovaApiClient.kt parses and it
is unchanged. `error`, `authority` and `gain` are added alongside, never
replacing, so the Android client needs no coordinated release: it reads the four
keys it always read and ignores the rest.

READING THE LOG BACK
from_episode() holds every shape the `action` column has ever been written in -
see its docstring. It is the only place that knows about them, so a reader that
wants Actions out of an Episode does not need to know the project's history.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# How a call came about. `requested` is a reference step - the user asked, so it
# runs whatever the dials say. `inferred` is a disturbance correction - NOVA
# acting on divergence it observed, which is the only thing Controller Gain
# governs.
Trigger = Literal["requested", "inferred"]

# The four keys Android parses. Named so nothing can quietly drop one.
WIRE_FIELDS = ("tool", "input", "trigger", "ran")


class Action(BaseModel):
    """One Tool call, with the parameters that were resolved for it and whether
    the Controller let it run.
    """

    tool: str
    input: dict[str, Any] = Field(default_factory=dict)
    trigger: Trigger = "requested"
    ran: bool = False

    error: Optional[float] = None
    authority: Optional[float] = None
    gain: Optional[float] = None

    def for_wire(self) -> dict[str, Any]:
        """This Action as the phone and the Episode see it.

        The four frozen keys always, the control trace only where there is one -
        so a reactive call serialises to exactly the dict the pre-overhaul code
        produced, byte for byte.
        """
        wire: dict[str, Any] = {
            "tool": self.tool,
            "input": dict(self.input),
            "trigger": self.trigger,
            "ran": self.ran,
        }
        for name in ("error", "authority", "gain"):
            value = getattr(self, name)
            if value is not None:
                wire[name] = value
        return wire

    @classmethod
    def from_episode(cls, action_column: Any) -> list[Action]:
        """Every Action in one episode's `action` column, whatever shape it is in.

        Refused Actions are returned rather than filtered: parsing is faithful
        and filtering is the caller's business. Malformed entries are skipped, so
        one bad row cannot take out a consolidation pass over the whole log.
        """
        if not isinstance(action_column, dict):
            return []

        entries = action_column.get("actions")
        if not isinstance(entries, list):
            entries = action_column.get("calls")

        if isinstance(entries, list):
            return [a for a in (cls._one(e) for e in entries) if a is not None]

        if action_column.get("tool"):
            single = cls._one({
                "tool": action_column["tool"],
                "input": action_column.get("params") or {},
            })
            return [single] if single is not None else []

        return []

    @classmethod
    def _one(cls, entry: Any) -> Optional[Action]:
        """One log entry, or None if it is not readable as an Action."""
        if not isinstance(entry, dict) or not entry.get("tool"):
            return None
        # `params` is the seeded shape's name for `input`; both appear inside
        # `calls` lists too, so accept either.
        raw_input = entry.get("input")
        if not isinstance(raw_input, dict):
            raw_input = entry.get("params")
        trigger = entry.get("trigger")
        return cls(
            tool=str(entry["tool"]),
            input=raw_input if isinstance(raw_input, dict) else {},
            trigger=trigger if trigger in ("requested", "inferred") else "requested",
            ran=bool(entry.get("ran", True)),
            error=_as_float(entry.get("error")),
            authority=_as_float(entry.get("authority")),
            gain=_as_float(entry.get("gain")),
        )


def _as_float(value: Any) -> Optional[float]:
    """A stored trace value, or None. jsonb round-trips ints as ints."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
