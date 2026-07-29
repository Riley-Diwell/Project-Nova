"""
schemas/tool_gain.py - Section 5.7: Controller Gain, over the wire

WHAT THIS FILE IS
The shapes GET/PUT /tools/gain speak (main.py), so the Android app's Gain tab
(ui/screens/GainScreen.kt) can list every Function tool and dial its gain.

Mirrors gain/controller_gain.py: a tool carries a learned `value` and an
optional user `override`, and `effective` is whichever the dispatcher will
actually use. The dial writes `override`; nothing in the UI touches `value`.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ToolGainOut(BaseModel):
    """One registered Function tool and its gain."""
    name: str
    description: str
    value: float = Field(..., ge=0.0, le=1.0, description="Learned gain.")
    override: float | None = Field(
        None, ge=0.0, le=1.0, description="User-set gain; None if not overridden."
    )
    effective: float = Field(
        ..., ge=0.0, le=1.0, description="What the dispatcher uses: override if set, else value."
    )


class ToolGainUpdate(BaseModel):
    """What the dial posts back. `override: null` clears it, reverting the tool
    to its learned value - that is the Reset button, not a validation error."""
    override: float | None = Field(None, ge=0.0, le=1.0)
