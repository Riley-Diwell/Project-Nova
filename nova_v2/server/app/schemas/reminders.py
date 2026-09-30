"""
schemas/reminders.py - reminder sync, over the wire  (Riley)

The shapes POST /reminders/sync speaks (api/reminders.py). A reminder is the
phone's ReminderEntity as `data`, which the server stores without
interpreting; the other fields are what sync itself needs.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

# Generous for one reminder's fields; stops the table being used as free storage.
MAX_DATA_BYTES = 8_000


class ReminderSyncItem(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    status: str = Field(..., min_length=1, max_length=16)
    updated_at_ms: int = Field(..., ge=0, description="The phone's clock at its last edit.")
    data: dict[str, Any]

    @field_validator("data")
    @classmethod
    def _not_too_big(cls, data: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(data)) > MAX_DATA_BYTES:
            raise ValueError(f"reminder data over {MAX_DATA_BYTES} bytes")
        return data


class ReminderSyncIn(BaseModel):
    since: datetime | None = Field(None, description="The cursor from the last sync; null = everything.")
    changes: list[ReminderSyncItem] = Field(default_factory=list, max_length=500)


class ReminderSyncOut(BaseModel):
    reminders: list[ReminderSyncItem]
    cursor: datetime | None = Field(None, description="Send as `since` next time.")
