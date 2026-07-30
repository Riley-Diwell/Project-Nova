"""
tools/calendar_tool.py - the user's calendar, read and written.

WHAT THIS FILE IS
The registry entries for get_calendar_range and add_calendar_event. Neither
runs here: the calendar lives on the phone, in Android's Calendar Provider.
They differ in whether anything has to come back.

  get_calendar_range  the model needs the answer before it can speak, so the
                      Intent Surface pauses the conversation (loop.py's
                      CLIENT_TOOLS), hands the range to Android, and resumes
                      with whatever the device sends back. _execute() is
                      unreachable in normal operation and says so if reached.
  add_calendar_event  nothing has to come back, so there is no reason to pause.
                      The Action recorded when it runs IS the instruction - it
                      goes out in EventOut.actions and the phone writes it.

WHY THEY ARE REGISTERED AT ALL
Registration is what gives a tool a controller gain, and therefore a dial in
the Android app's Gain tab. Both are worth tuning - high gain means Nova
volunteers what is coming up, or schedules a plan you merely mentioned; zero
means it only acts when asked - even though execution happens off-box.

Being registered also means loop.py builds their Claude-facing definitions from
here rather than keeping a second copy inline.
"""

from typing import Any, Optional

from .base import BaseTool

# How much commitment in the horizon counts as maximal divergence. Two imminent
# things is the case most worth volunteering: it is where the user is most likely
# to have lost track of one of them. Beyond that the answer is already "your next
# hour is full", and a larger number would only distort the gain.
CALENDAR_SATURATION = 2.0


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
                "from the top-level local_time and from nothing else - in "
                "particular not from the triggering event's timestamp, which "
                "is UTC and is frequently a different calendar day from the "
                "user's. So if local_time is 2026-07-29T02:04, 'today' "
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

    def error(self, observation: Any) -> Optional[float]:
        """How much the next hour commits the user to, in [0, 1].

        The measurement: each commitment inside the horizon contributes its own
        imminence - something starting now weighs a full point, something at the
        far edge of the horizon weighs almost nothing - and the total saturates at
        CALENDAR_SATURATION.

            imminence(c) = (horizon - minutes_until_start) / horizon
            error        = clamp(sum(imminence) / CALENDAR_SATURATION)

        So an empty hour is zero, one distant entry is close to zero, and a
        crowded imminent hour saturates. Nothing about *which* entries: what the
        user is committed to is the model's to phrase, and this only measures how
        much of it there is.

        WHAT THIS TERM DOES NOT MEASURE, AND WHY
        Spec 0001 defines this as divergence between what the next hour commits
        the user to and *what they have been told about it this session*. The
        second half is not implemented, and cannot be as things stand: it is
        session state, and the Observer reads only the Event, the User State and
        Persona - deliberately not Memory, which is a network call in the request
        path. So this term measures commitment alone.

        The consequence is honest and worth knowing: at a high gain, this tool can
        volunteer the same look-ahead twice in a session, because nothing here
        remembers having done it. Adding a told-this-session signal to the
        Observation is the fix; it needs a durable turn-scoped store that V1 does
        not have.
        """
        commitments = getattr(observation, "commitments", None) or []
        if not commitments:
            return 0.0

        horizon = getattr(observation, "horizon_minutes", 60) or 60
        weight = sum(
            max(0.0, (horizon - c.minutes_until_start) / horizon)
            for c in commitments
        )
        return round(min(1.0, weight / CALENDAR_SATURATION), 4)

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


class AddCalendarEventTool(BaseTool):
    """
    Writing an event into the user's calendar.

    The work happens on the phone, but unlike get_calendar_range nothing has to
    come back: the model does not need a result to finish speaking, so there is
    no reason to pause the conversation for a device round-trip.

    So this tool's whole job is to succeed. The Action recorded when it runs -
    {tool, input, trigger, ran} - IS the instruction: it travels out in
    EventOut.actions, the phone executes it via CalendarWriter, and the same
    record lands in the Episode as evidence of what NOVA did. There is no second
    structure and no queue; the log of what happened and the instruction to make
    it happen are the same object (CONTEXT.md "Action").
    """

    def __init__(self) -> None:
        super().__init__(
            name="add_calendar_event",
            description=(
                "Adds an event to the user's calendar. Use this when they ask "
                "you to schedule, book or put something in their calendar, and "
                "when a plan they state has a definite time. The event is "
                "written on the device, so confirm it in speech as done rather "
                "than as pending. Times are the user's LOCAL time, ISO 8601 "
                "with no timezone suffix, exactly as get_calendar_range takes "
                "them - work from the top-level local_time, never from the "
                "triggering event's UTC timestamp. If they name a start but no "
                "end, give it a sensible duration rather than asking."
            ),
            gain_description=(
                "How readily Nova puts things in your calendar without being "
                "asked outright. At 1.0 a plan you simply mention out loud - "
                "'coffee with Sam on Thursday at ten' - gets scheduled. At 0.0 "
                "it only adds events you explicitly ask it to add."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "What the event is called, in the user's own words.",
                    },
                    "start_time": {
                        "type": "string",
                        "description": (
                            "Start in the user's LOCAL time, ISO 8601 with no "
                            "timezone suffix - e.g. 2026-07-29T10:00:00."
                        ),
                    },
                    "end_time": {
                        "type": "string",
                        "description": (
                            "End in the user's LOCAL time, same format. Required: "
                            "pick a sensible duration if the user did not say one."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional detail - where, with whom, anything they added.",
                    },
                },
                "required": ["title", "start_time", "end_time"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        # Nothing to do here on purpose - see the class docstring. Returned
        # rather than raising so the model can speak a confident confirmation,
        # and echoing the details back so it confirms what was actually booked
        # rather than what it meant to book.
        return {
            "success": True,
            "queued_for_device": True,
            "title": tool_input.get("title"),
            "start_time": tool_input.get("start_time"),
            "end_time": tool_input.get("end_time"),
        }
