"""
navigation_departure_time - Function tool: when does the user need to leave
to get somewhere on time.

CHANGES
  - Now uses maps_client.py instead of calling requests directly
  - Returns full transit steps (line numbers, stop names, walk legs)
  - Proactive trigger: if loop.py injects a calendar event as destination,
    the tool fires before the user asks
  - origin injected by loop.py from user_state.location_ctx (unchanged)

PHRASES THAT SHOULD MAP HERE
  "when do I leave", "how long to get to", "what time should I head off",
  "when should I leave for", "how do I get to", "am I going to make it"
"""

from typing import Any

from .base import BaseTool

try:
    from ..maps_client import get_directions
except ImportError:
    from maps_client import get_directions

DEFAULT_HOME = {"lat": -35.2809, "lng": 149.1300}


class NavigationTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="navigation_departure_time",
            description=(
                "Use when the user asks when to leave, how long to get "
                "somewhere, or if they will make it on time. Returns "
                "departure time, travel duration, and transit steps as a "
                "spoken sentence. Success: user gets a clear leave-by time "
                "without opening a map."
            ),
            gain_description=(
                "How readily Nova suggests when to leave without being asked. "
                "At 1.0 it checks travel time whenever it thinks you might "
                "need to go somewhere soon — a calendar event, a location "
                "change. At 0.0 it only calculates if you ask directly."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "destination": {
                        "type": "string",
                        "description": "Where the user wants to go. Place name or address.",
                    },
                    "arrival_time": {
                        "type": "string",
                        "description": (
                            "When they need to arrive, e.g. '9am', '14:30'. "
                            "Omit to get travel time from now."
                        ),
                    },
                    "origin": {
                        "type": "string",
                        "description": (
                            "Where they are leaving from. "
                            "loop.py injects user_state.location_ctx here "
                            "before dispatch — only set manually if overriding."
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["transit", "walking", "driving"],
                        "description": "How they are travelling. Defaults to transit.",
                    },
                    "trigger": {
                        "type": "string",
                        "enum": ["requested", "inferred"],
                        "description": (
                            "'requested' when the user asked directly. "
                            "'inferred' when Nova detected an upcoming calendar "
                            "event and is suggesting proactively."
                        ),
                    },
                },
                "required": ["destination"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        destination  = tool_input.get("destination", "")
        arrival_time = tool_input.get("arrival_time")
        origin       = (
            tool_input.get("origin")
            or f"{DEFAULT_HOME['lat']},{DEFAULT_HOME['lng']}"
        )
        mode = tool_input.get("mode", "transit")

        if not destination:
            return {
                "success": False,
                "spoken":  "Where would you like to go?",
                "needs_clarification": True,
            }

        return get_directions(
            origin=origin,
            destination=destination,
            arrival_time=arrival_time,
            mode=mode,
        )
