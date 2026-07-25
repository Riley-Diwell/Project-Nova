"""
Defines the standard format for describing a tool.

Every tool in Nova has a ToolSchema which tells the intent surface:
    - what the tool is called
    - what it does
    - what inputs it needs
    - how proactive vs reactive it is (gain)


Example use:

ToolSchema(
    name="query_memory",
    description="Search stored user memories and past events.",
    input_schema={...},
    gain=0.5,
)
"""

from typing import Any
from pydantic import BaseModel, Field


class ToolSchema(BaseModel):
    name: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9_-]{1,128}$",      # allowed characters
        description=(
            "Unique name used to call this tool"
        ),
    )
    description: str = Field(
        ...,
        min_length=1,
        description=(
            "Short simple explination of what the tool does"
            "The model will use this when it decides whether to call this tool"
        ),
    )
    input_schema: dict[str, Any] = Field(
        ...,
        description="JSON Schema describing this tool's expected inputs.",
    )
    gain: float = Field(
        ...,
        ge=0.0,     # greater than or equal to
        le=1.0,     # less than or equal to
        description=(
            "Current tool gain value between 0 and 1."
        ),
    )

    # if configuration changes create a new schema
    model_config = {"frozen": True}  