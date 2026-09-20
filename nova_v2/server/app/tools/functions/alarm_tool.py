"""
tools/alarm_tool.py - set_timer and set_alarm: NOVA calling out to the
device's own Clock app.

WHAT THIS FILE IS
Neither tool touches an alarm/timer system of its own. The work happens on
the phone via android.provider.AlarmClock's ACTION_SET_TIMER/ACTION_SET_ALARM
intents - implicit intents that hand off to whatever clock app is installed,
the same "let the real app do the real work" shape navigation.py hands routing
to Google Maps for. Same fire-and-forget contract as add_calendar_event too:
nothing has to come back, so the Action recorded when either tool runs IS the
instruction - it travels out in EventOut.actions, Android fires the intent via
AlarmIntents.kt, and the same record lands in the Episode as evidence of what
NOVA did.

WHY THEY ARE REGISTERED AT ALL
Registration is what gives a tool a controller gain, consistent with every
other device-write tool (calendar_tool.py) - even though execution happens
off-box and neither tool below measures its own error() (open-loop, per
BaseTool: they only ever run on a classified command, never proactively
inferred from divergence). The dial still matters for how readily NOVA acts on
something merely mentioned in passing ("I should set a timer for the oven")
versus only when asked outright.
"""

from typing import Any

from app.tools.core.base import BaseTool


class SetTimerTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="set_timer",
            description=(
                "Starts a countdown timer on the user's phone via the device's "
                "Clock app. Use this when the user asks for a timer or a "
                "countdown with no specific clock time - 'set a timer for 10 "
                "minutes', 'remind me in an hour', 'ping me in 90 seconds'. "
                "Convert whatever duration they said into whole seconds "
                "yourself (10 minutes -> 600, 1 hour -> 3600, 90 seconds -> "
                "90) - never pass the phrase itself. The timer starts the "
                "moment this call is made, so confirm it in speech as done, "
                "e.g. 'timer set for 10 minutes', not as pending."
            ),
            gain_description=(
                "How readily Nova starts a timer without being asked outright "
                "- e.g. starting one unprompted off something you said in "
                "passing. At 1.0 it acts on a stated need. At 0.0 it only "
                "starts a timer you explicitly ask for."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "duration_seconds": {
                        "type": "integer",
                        "description": (
                            "How long the timer runs for, in whole seconds - "
                            "converted from whatever the user said (minutes, "
                            "hours, a mix)."
                        ),
                    },
                    "label": {
                        "type": "string",
                        "description": (
                            "Optional short label for what the timer is for, "
                            "e.g. 'pasta' - only set this if the user named "
                            "one."
                        ),
                    },
                },
                "required": ["duration_seconds"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        # Nothing to do here on purpose - see the module docstring. Returned
        # rather than raising so the model can speak a confident confirmation,
        # echoing back what was actually queued rather than what it meant to.
        return {
            "success": True,
            "queued_for_device": True,
            "duration_seconds": tool_input.get("duration_seconds"),
            "label": tool_input.get("label"),
        }


class SetAlarmTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="set_alarm",
            description=(
                "Sets an alarm on the user's phone via the device's Clock "
                "app, for a specific clock time - 'wake me up at 7', 'set an "
                "alarm for 6:30am'. hour/minute are the user's LOCAL wall "
                "clock (top-level local_time), never UTC and never converted. "
                "If they gave a duration instead of a clock time ('wake me in "
                "20 minutes'), use set_timer instead - it is the same device, "
                "just the wrong tool for a relative ask. The alarm is set the "
                "moment this call is made, so confirm it in speech as done, "
                "e.g. 'alarm set for 6:30am', not as pending."
            ),
            gain_description=(
                "How readily Nova sets an alarm without being asked outright. "
                "At 1.0 it acts on a stated need - 'I should be up by 7' gets "
                "one set. At 0.0 it only sets an alarm you explicitly ask for."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "hour": {
                        "type": "integer",
                        "description": "Hour, 0-23, in the user's local time (24-hour).",
                    },
                    "minute": {
                        "type": "integer",
                        "description": "Minute, 0-59.",
                    },
                    "label": {
                        "type": "string",
                        "description": (
                            "Optional short label for what the alarm is for - "
                            "only set this if the user named one."
                        ),
                    },
                },
                "required": ["hour", "minute"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        return {
            "success": True,
            "queued_for_device": True,
            "hour": tool_input.get("hour"),
            "minute": tool_input.get("minute"),
            "label": tool_input.get("label"),
        }
