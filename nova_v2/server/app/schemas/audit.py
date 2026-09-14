"""
schemas/audit.py - Section: Autonomy pillar, the algorithmic/AI audit log

WHAT THIS FILE IS
The shape GET /audit speaks (main.py), so the Android app's Audit tab
(ui/screens/AuditLogScreen.kt) can list every automated action NOVA has
taken, and why.

One entry is one Action from one Episode - main.py flattens each episode's
`action.actions[]` (see tools/core/action.py) into individual rows, newest
episode first. `reason` is the Controller's Decision.reason (e.g.
"commanded", "zero_gain") - present here because this endpoint is the audit
trail; it is deliberately absent from what the model itself gets to read
back (see intent_surface.py's _redact_control_trace).

`context` and `summary` are plain-English, computed server-side by
tools/core/narration.py from the episode's raw `event` and the action's raw
`tool`/`input` - the phone should show these rather than trying to
reconstruct sentences from the wire's internal shapes itself.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class AuditEntryOut(BaseModel):
    """One Tool call, as the audit log shows it."""

    episode_id: str
    occurred_at: str | None = None
    event_type: str
    context: str | None = None
    tool: str
    input: dict[str, Any]
    trigger: str
    ran: bool
    reason: str | None = None
    error: float | None = None
    authority: float | None = None
    gain: float | None = None
    speech: str | None = None
    summary: str
