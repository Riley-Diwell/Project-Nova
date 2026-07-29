"""
tools/calendar_tool.py - reading the user's calendar over an arbitrary range.

WHAT THIS FILE IS
The registry entry for get_calendar_range. Unusual among the Function tools in
that it does not run here: the calendar lives on the phone, so the Intent
Surface pauses the conversation (loop.py's CLIENT_TOOLS), hands the range to
Android, and resumes with whatever the device sends back. _execute() is
therefore unreachable in normal operation, and says so if it is ever reached.

WHY IT IS REGISTERED AT ALL
Registration is what gives a tool a controller gain, and therefore a dial in
the Android app's Gain tab. Calendar reads are worth tuning for the same reason
the others are - high gain means Nova volunteers what is coming up, zero means
it only looks when asked - even though the execution happens off-box.

Being registered also means loop.py builds its Claude-facing definition from
here rather than keeping a second copy inline.
"""

from typing import Any

from .base import BaseTool


class CalendarTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="get_calendar_range",
            description=(
                "Reads the user's calendar live from their device over an "
                "arbitrary date range. Call this whenever a calendar question "
                "reaches outside the current_events/upcoming_events already "
                "present in user_state (e.g. 'today', 'tomorrow', 'next week', "
                "'next month', a specific date). Never fabricate calendar "
                "events you don't have - call this instead. "
                "Work entirely in the user's LOCAL time, not UTC. Take 'now' "
                "from user_state.local_time and from nothing else - in "
                "particular not from the triggering event's timestamp, which "
                "is UTC and is frequently a different calendar day from the "
                "user's. So if local_time is 2026-07-29T02:04+10:00, 'today' "
                "is the 29th and 'tomorrow' runs 2026-07-30T00:00:00 to "
                "2026-07-31T00:00:00. Events come back with start_local and "
                "end_local already in their timezone - quote those, and never "
                "convert start_millis yourself."
            ),
            gain_description=(
                "How readily Nova looks ahead in your calendar without being "
                "asked. At 1.0 it checks around whatever you are doing and "
                "volunteers what it finds - a clash, the next thing, when a "
                "free stretch runs out. At 0.0 it only opens your calendar to "
                "answer a question that needs it, and never raises the subject "
                "itself."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "from_time": {
                        "type": "string",
                        "description": (
                            "Start of the range in the user's LOCAL time, ISO 8601 "
                            "with no timezone suffix - e.g. 2026-07-29T00:00:00. "
                            "Do not append 'Z' and do not convert to UTC; the phone "
                            "reads this in its own timezone."
                        ),
                    },
                    "to_time": {
                        "type": "string",
                        "description": (
                            "End of the range in the user's LOCAL time, ISO 8601 with "
                            "no timezone suffix, e.g. 2026-07-30T00:00:00."
                        ),
                    },
                },
                "required": ["from_time", "to_time"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        # loop.py intercepts this tool by name before dispatch (CLIENT_TOOLS) and
        # routes it to the phone, so reaching here means that interception broke.
        return {
            "success": False,
            "error": (
                "get_calendar_range is resolved on the device, not in the backend - "
                "it should have been intercepted as a client tool."
            ),
        }
