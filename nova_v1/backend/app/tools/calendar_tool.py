"""
tools/calendar_tool.py - the user's calendar, read and written.

WHAT THIS FILE IS
The registry entries for get_calendar_range, add_calendar_event,
edit_calendar_event and delete_calendar_event. None of them run here: the
calendar lives on the phone, in Android's Calendar Provider. They differ in
whether anything has to come back.

  get_calendar_range     the model needs the answer before it can speak, so
                         the Intent Surface pauses the conversation (loop.py's
                         CLIENT_TOOLS), hands the range to Android, and
                         resumes with whatever the device sends back.
                         _execute() is unreachable in normal operation and
                         says so if reached.
  add_calendar_event     nothing has to come back, so there is no reason to
                         pause. The Action recorded when it runs IS the
                         instruction - it goes out in EventOut.actions and the
                         phone writes it.
  edit_calendar_event    same fire-and-forget shape as add - the phone applies
                         the change the moment the Action arrives, no
                         confirmation, because an edit is easily undone.
  delete_calendar_event  same fire-and-forget shape as add, but the phone
                         does not act on the Action the moment it arrives -
                         it always confirms with the user first (see the
                         class docstring below), because a delete is not.

WHY THEY ARE REGISTERED AT ALL
Registration is what gives a tool a controller gain, and therefore a dial in
the Android app's Gain tab. All four are worth tuning - high gain means Nova
volunteers what is coming up, schedules a plan you merely mentioned, applies
a restated change, or offers to remove something outright; zero means it
only acts when asked - even though execution happens off-box.

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

# Shared by AddCalendarEventTool and EditCalendarEventTool - both hand the phone
# the same plain frequency/interval/count/until fields rather than an RRULE
# string, so NovaApiClient.buildRrule is the only place that has to get RFC
# 5545 right.
_RECURRENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "frequency": {
            "type": "string",
            "enum": ["daily", "weekly", "monthly", "yearly"],
            "description": "How often it repeats.",
        },
        "interval": {
            "type": "integer",
            "description": (
                "Every how many frequency-units - 1 for 'every week', 2 for "
                "'every other week'. Defaults to 1 if omitted."
            ),
        },
        "count": {
            "type": "integer",
            "description": (
                "Stop after this many occurrences, e.g. 10 for 'the next 10 "
                "weeks'. Leave both count and until out for something with no "
                "stated end, e.g. a standing weekly meeting."
            ),
        },
        "until": {
            "type": "string",
            "description": (
                "Last possible occurrence date, in the user's LOCAL time, same "
                "format as start_time - e.g. 'through the end of August'. At "
                "most one of count/until should be set, never both."
            ),
        },
    },
    "required": ["frequency"],
}


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
                    "recurrence": {
                        **_RECURRENCE_SCHEMA,
                        "description": (
                            "Only set this if the user described something repeating "
                            "('every Monday', 'daily standup', 'monthly rent') - omit "
                            "it entirely for a one-off event. The phone builds the "
                            "actual recurrence rule from these fields, so give plain "
                            "values rather than trying to construct one yourself."
                        ),
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
            "recurrence": tool_input.get("recurrence"),
        }


class EditCalendarEventTool(BaseTool):
    """
    Changing an existing event on the user's calendar - same fire-and-forget
    shape as AddCalendarEventTool: nothing has to come back, so the Action
    recorded here IS the instruction, and the phone applies it via
    CalendarWriter.updateEvent without pausing for a confirmation, exactly
    like an add. An edit is easily undone (edit it back, or delete it), so it
    doesn't carry delete_calendar_event's need for an on-device confirm gate.

    Only the fields the user actually wants changed should be set - anything
    left out keeps its current value, which is why every field but event_id
    is optional here (contrast AddCalendarEventTool, which requires the
    whole event because there's nothing existing to fall back to).
    """

    def __init__(self) -> None:
        super().__init__(
            name="edit_calendar_event",
            description=(
                "Changes an existing event on the user's calendar. Requires "
                "the event's event_id, which only comes from a prior "
                "get_calendar_range call this turn or in recent_episodes - "
                "call get_calendar_range first if you don't already have it "
                "for the event the user means. Never guess an event_id. Set "
                "only the fields that are actually changing - leave the rest "
                "out and they keep their current value. The change is written "
                "on the device the same way add_calendar_event is, so confirm "
                "it in speech as done rather than as pending."
            ),
            gain_description=(
                "How readily Nova changes an existing calendar entry without "
                "being asked outright - moving a meeting the user mentions is "
                "now earlier, for instance. At 1.0 a restated plan gets "
                "applied to the existing event. At 0.0 it only changes an "
                "event when explicitly asked to."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "integer",
                        "description": (
                            "The event's id, exactly as returned by "
                            "get_calendar_range - never invented or guessed."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "New title, only if it's changing.",
                    },
                    "start_time": {
                        "type": "string",
                        "description": (
                            "New start, in the user's LOCAL time, same format "
                            "as add_calendar_event - only if it's changing."
                        ),
                    },
                    "end_time": {
                        "type": "string",
                        "description": (
                            "New end, same format - only if it's changing. If "
                            "start_time moves but the event's length should "
                            "stay the same, give the shifted end_time too "
                            "rather than leaving it out."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "New detail text, only if it's changing.",
                    },
                    "recurrence": {
                        **_RECURRENCE_SCHEMA,
                        "description": (
                            "Only set this if the user wants to change how the "
                            "event repeats - leave the whole field out to keep "
                            "its recurrence as it is."
                        ),
                    },
                },
                "required": ["event_id"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        return {
            "success": True,
            "queued_for_device": True,
            "event_id": tool_input.get("event_id"),
            "title": tool_input.get("title"),
            "start_time": tool_input.get("start_time"),
            "end_time": tool_input.get("end_time"),
            "description": tool_input.get("description"),
            "recurrence": tool_input.get("recurrence"),
        }


class DeleteCalendarEventTool(BaseTool):
    """
    Removing an event from the user's calendar.

    Like AddCalendarEventTool, the actual work happens on the phone and there
    is nothing to wait for, so this runs the same fire-and-forget way: the
    Action recorded here goes out in EventOut.actions and CalendarWriter
    carries it out.

    THE DIFFERENCE: deletion is destructive and, unlike an add, cannot be
    undone by the user simply ignoring it. So the phone does NOT delete on
    receipt of this Action the way it writes an add_calendar_event Action
    immediately - it always shows a confirm dialog first (VoiceScreen.kt) and
    only calls CalendarWriter.deleteEvent if the user taps yes. That gate
    lives on the device and fires regardless of gain or of anything the model
    says, so speech should describe the deletion as pending confirmation, not
    as done.
    """

    def __init__(self) -> None:
        super().__init__(
            name="delete_calendar_event",
            description=(
                "Deletes an event from the user's calendar. Requires the "
                "event's event_id, which only comes from a prior "
                "get_calendar_range call this turn or in recent_episodes - "
                "call get_calendar_range first if you don't already have it "
                "for the event the user means. Never guess an event_id. The "
                "phone always asks the user to confirm before actually "
                "deleting, so speak about this as something you're checking "
                "with them on, not as already done - e.g. 'I'll check with "
                "you on your phone before removing that', not 'done, it's "
                "deleted'."
            ),
            gain_description=(
                "How readily Nova offers to remove things from your calendar "
                "without being asked outright. At 1.0 it will act on a stated "
                "plan like 'cancel my coffee with Sam'. At 0.0 it only removes "
                "an event when you explicitly ask it to. Either way, the phone "
                "always confirms with you before anything is actually deleted."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "integer",
                        "description": (
                            "The event's id, exactly as returned by "
                            "get_calendar_range - never invented or guessed."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": (
                            "The event's title, as seen from get_calendar_range - "
                            "shown to the user on the confirm dialog so they know "
                            "what they're being asked about."
                        ),
                    },
                },
                "required": ["event_id", "title"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        return {
            "success": True,
            "queued_for_device": True,
            "pending_user_confirmation": True,
            "event_id": tool_input.get("event_id"),
            "title": tool_input.get("title"),
        }
