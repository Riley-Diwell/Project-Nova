"""Data structure for the Memory store
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class Action(BaseModel):
    """The Tool that the Intent Surface dispatched."""

    tool: str
    params: dict[str, Any] = Field(default_factory=dict)


class Outcome(BaseModel):
    """How the action resolved. Written in a second phase, after the user reacts.

    `status` values used by Controller Gain reinforcement:
      - "accepted" / "kept"   -> nudge gain up
      - "rejected" / "undone" -> nudge gain down
      - "completed"           -> reactive action finished, neutral
    Kept as a free string so it can extend without a schema change.
    """

    status: str
    user_response: Optional[str] = None
    gain_delta: Optional[float] = None
    detail: dict[str, Any] = Field(default_factory=dict)


class MemoryEntry(BaseModel):
    """One episodic record: {event, user_state, action, outcome}."""

    event: dict[str, Any]
    action: Action
    user_state: dict[str, Any] = Field(default_factory=dict)
    outcome: Optional[Outcome] = None

    # Set by the store on append; do not populate by hand.
    id: Optional[str] = None
    created_at: Optional[datetime] = None


class MemoryFilter(BaseModel):
    """Read filter for `memory.query`."""

    tool: Optional[str] = None
    status: Optional[str] = None       # matches outcome.status
    since: Optional[datetime] = None   # created_at >= since
    until: Optional[datetime] = None   # created_at <= until
    limit: int = 50
