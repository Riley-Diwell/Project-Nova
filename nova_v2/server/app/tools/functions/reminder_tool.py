"""
tools/functions/reminder_tool.py - set_reminder, update_reminder and
get_reminders: reminders that Nova itself delivers.

WHAT THIS FILE IS
The registry entries for the three reminder tools. None of them keep any state
here: reminders live on the phone, in a Room table Nova owns
(ReminderRepository.kt). The phone is the only source of truth, because it is
the only place an alarm can fire - Cloud Run scales to zero and cannot push.
This is the calendar's shape exactly (calendar_tool.py):

  set_reminder      fire-and-forget. The Action recorded when it runs IS the
                    instruction - it travels out in EventOut.actions and the
                    phone inserts the row and schedules the alarm.
  update_reminder   same fire-and-forget shape, for complete / snooze / edit /
                    delete. Delete is a soft delete with Undo on the phone, so
                    unlike delete_calendar_event there is no confirm gate.
  get_reminders     a CLIENT_TOOL like get_calendar_range: the model needs the
                    answer before it can speak, so the Intent Surface pauses,
                    the phone reads its own table, and the turn resumes.
                    _execute() is unreachable in normal operation.

The model otherwise reads reminders from user_state.reminders - a bounded
window the phone attaches to voice turns only - the same way it reads
current_events.

TIME
Reminder times cross the wire as the user's LOCAL wall clock with no suffix
(or as in_minutes for a relative ask), never as UTC. The server only knows
today's utc_offset_minutes; the phone knows the zone rules, so "Monday 9am"
stays 9am across a daylight-saving change the server cannot see.

VALIDATION
Unlike the calendar tools these validate their input, and return
{"success": False, "error": ...} rather than raising (nothing would catch it).
intent_surface._run_local_tool records a failed DEVICE_TOOLS call with
ran=False, so the phone - which skips ran=false Actions - never carries out an
instruction the tool itself rejected, and the model gets the error to re-ask
with ("9 has passed - did you mean 9pm or tomorrow?").
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from app.tools.core.base import BaseTool
from app.tools.functions.calendar_tool import _RECURRENCE_SCHEMA

# A year, in minutes. Anything further out is almost certainly a mis-parse.
MAX_IN_MINUTES = 527_040

# How far into the past a due_local may be before it is rejected. A little
# slack, because "remind me at 4:30" said at 4:30:40 is a reasonable request.
PAST_TOLERANCE = timedelta(minutes=2)

DEFAULT_SNOOZE_MINUTES = 10

PRIORITIES = ("normal", "important")
UPDATE_ACTIONS = ("complete", "snooze", "edit", "delete")
FREQUENCIES = ("daily", "weekly", "monthly", "yearly")


def _now_utc() -> datetime:
    """The one clock read in this module - a function so tests can pin it."""
    return datetime.now(timezone.utc)


def _local_now(tool_input: dict[str, Any]) -> datetime:
    """The user's wall clock, naive, from the offset _run_local_tool injects."""
    offset = tool_input.get("utc_offset_minutes")
    offset = offset if isinstance(offset, int) and not isinstance(offset, bool) else 0
    return (_now_utc() + timedelta(minutes=offset)).replace(tzinfo=None)


def _fail(message: str) -> dict[str, Any]:
    return {"success": False, "error": message}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_due_local(value: Any, tool_input: dict[str, Any]) -> Optional[str]:
    """None if `value` is a usable local due time, otherwise why not."""
    if not isinstance(value, str) or not value.strip():
        return "due_local must be a local ISO 8601 time like 2026-09-23T16:30:00."
    try:
        due = datetime.fromisoformat(value.strip())
    except ValueError:
        return f"due_local {value!r} is not ISO 8601 - use e.g. 2026-09-23T16:30:00."
    if due.tzinfo is not None:
        return (
            "due_local must be the user's LOCAL wall clock with no timezone suffix "
            "or 'Z' - drop the offset and don't convert to UTC."
        )
    now = _local_now(tool_input)
    if due < now - PAST_TOLERANCE:
        return (
            f"{value} has already passed (it is now {now.strftime('%Y-%m-%dT%H:%M')} "
            "for the user). Ask which they meant - later today, or another day."
        )
    return None


def _check_in_minutes(value: Any) -> Optional[str]:
    if not _is_int(value) or not 1 <= value <= MAX_IN_MINUTES:
        return f"in_minutes must be a whole number of minutes from 1 to {MAX_IN_MINUTES}."
    return None


def _check_recurrence(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("frequency") not in FREQUENCIES:
        return f"recurrence.frequency must be one of {', '.join(FREQUENCIES)}."
    interval = value.get("interval")
    if interval is not None and (not _is_int(interval) or interval < 1):
        return "recurrence.interval must be a whole number of at least 1."
    if value.get("count") is not None and value.get("until") is not None:
        return "Set at most one of recurrence.count and recurrence.until."
    return None


def _has(tool_input: dict[str, Any], key: str) -> bool:
    return tool_input.get(key) is not None


class SetReminderTool(BaseTool):
    """
    Setting a reminder that Nova delivers at the right moment.

    Fire-and-forget like add_calendar_event: the Action recorded here is the
    instruction, and the phone stores and schedules it. Returned rather than
    raised on bad input so the model can re-ask - see the module docstring.
    """

    def __init__(self) -> None:
        super().__init__(
            name="set_reminder",
            description=(
                "Sets a reminder that Nova delivers at the right moment - a "
                "buzz on the wearable and a notification, held until after a "
                "class if it would interrupt one. Use it for anything with "
                "something to say at a time: 'remind me at 4:30 to email Dr "
                "Chen', 'remind me in 20 minutes to take the pasta off', "
                "'don't let me forget to submit the form by 5', 'remember to "
                "call Mum tonight' ('remember to ...' is a reminder, not a "
                "memory save). Not for a bare "
                "countdown (set_timer), a wake-up (set_alarm), or something "
                "with a place, people or a duration (add_calendar_event). "
                "For a relative time use in_minutes and never do the clock "
                "arithmetic yourself. For an absolute time use due_local in "
                "the user's LOCAL time, worked out from the top-level "
                "local_time. For 'after this lecture/class', copy that "
                "current_events entry's end_local exactly. If they gave no "
                "time at all, ask when rather than guessing. It is stored on "
                "the device the moment this call is made, so confirm it as "
                "done ('I'll remind you at 4:30'), not as pending."
            ),
            gain_description=(
                "How readily Nova sets a reminder you didn't ask for. At 0.0 "
                "it only sets one when you say 'remind me'. Higher up, "
                "something you say you need to do - 'I need to submit the "
                "form by 5' - gets a reminder, or a question about when you "
                "want one if you didn't say a time. Reminders Nova suggests "
                "are marked in the Reminders list; deleting them turns this "
                "dial down, finishing them turns it up."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": (
                            "What to remind them of, a short instruction in their "
                            "own words WITHOUT the time, e.g. 'Email Dr Chen about "
                            "the extension'."
                        ),
                    },
                    "due_local": {
                        "type": "string",
                        "description": (
                            "When, in the user's LOCAL time, ISO 8601 with no "
                            "timezone suffix (2026-09-23T16:30:00), worked out from "
                            "top-level local_time - never UTC. For 'after this "
                            "lecture/class' copy that current_events entry's "
                            "end_local exactly. Omit if you set in_minutes."
                        ),
                    },
                    "in_minutes": {
                        "type": "integer",
                        "minimum": 1,
                        "description": (
                            "For a relative ask ('in 20 minutes', 'in an hour and a "
                            "half') the whole minutes from now (20, 90). Do NOT add "
                            "it to local_time yourself - the phone counts from when "
                            "it receives this. Set exactly one of due_local / "
                            "in_minutes."
                        ),
                    },
                    "priority": {
                        "type": "string",
                        "enum": list(PRIORITIES),
                        "description": (
                            "'important' only if they said it matters or not to let "
                            "them miss it - it may arrive as a buzz during a class "
                            "instead of waiting. Default normal."
                        ),
                    },
                    "recurrence": {
                        **_RECURRENCE_SCHEMA,
                        "description": (
                            "Only for a repeating reminder ('every Monday at 9', "
                            "'every day at 8pm') - omit it entirely for a one-off. "
                            "due_local is then the FIRST occurrence."
                        ),
                    },
                },
                "required": ["text"],
            },
        )

    def error(self, observation: Any) -> Optional[float]:
        """How strongly the user just stated an obligation, in [0, 1].

        control/cues.py measures it deterministically from the words: 1.0 for
        an obligation with a time ("I need to submit the form by 5"), 0.5 for an
        obligation alone, 0.0 otherwise. It is 0.0 rather than None on ambient
        events - the tool did measure, and nobody stated anything - so an ambient
        tick is NO_DIVERGENCE and stays silent at any gain.
        """
        value = getattr(observation, "obligation_cue", 0.0)
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        text = tool_input.get("text")
        if not isinstance(text, str) or not text.strip():
            return _fail("text is required - what should the reminder say?")

        has_due, has_in = _has(tool_input, "due_local"), _has(tool_input, "in_minutes")
        if has_due == has_in:
            return _fail(
                "Set exactly one of due_local (a clock time) or in_minutes (relative). "
                "If the user gave no time, ask them when."
            )
        problem = (
            _check_due_local(tool_input["due_local"], tool_input) if has_due
            else _check_in_minutes(tool_input["in_minutes"])
        )
        if problem:
            return _fail(problem)

        priority = tool_input.get("priority") or "normal"
        if priority not in PRIORITIES:
            return _fail("priority must be 'normal' or 'important'.")

        problem = _check_recurrence(tool_input.get("recurrence"))
        if problem:
            return _fail(problem)

        return {
            "success": True,
            "queued_for_device": True,
            "text": text.strip(),
            "due_local": tool_input.get("due_local"),
            "in_minutes": tool_input.get("in_minutes"),
            "priority": priority,
            "recurrence": tool_input.get("recurrence"),
        }


class UpdateReminderTool(BaseTool):
    """
    Completing, snoozing, editing or deleting an existing reminder - one tool
    with an action enum, like memory's save/recall.

    Fire-and-forget. Delete is a soft delete the phone can undo, so it is
    applied on receipt rather than behind a confirm dialog.
    """

    def __init__(self) -> None:
        super().__init__(
            name="update_reminder",
            description=(
                "Completes, snoozes, edits or deletes one of the user's "
                "reminders. reminder_id comes only from user_state.reminders "
                "or a get_reminders result - never invent or guess one; if "
                "you don't have it, call get_reminders first. 'That' or 'it' "
                "right after a reminder went off means the reminder with the "
                "smallest fired_minutes_ago. complete: they did it. snooze: "
                "bring it back in in_minutes (default 10). edit: change text, "
                "or its time with due_local (a new LOCAL time), in_minutes "
                "(relative to now) or shift_minutes ('push it back half an "
                "hour' = 30, 'an hour earlier' = -60) - only one of those "
                "three. 'Change my X reminder to 4pm' is an edit with "
                "due_local on the reminder's own date; 'to Friday' keeps its "
                "own time; 'to say Y' is text. delete: remove it. All apply "
                "on the device the moment this call is made, so confirm them "
                "as done."
            ),
            gain_description=(
                "How readily Nova changes one of your reminders without being "
                "asked outright. Nova can't yet tell on its own that you've "
                "done something, so for now this only acts when you ask."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "reminder_id": {
                        "type": "string",
                        "description": (
                            "The id exactly as listed in user_state.reminders (or "
                            "a get_reminders result). Never invent or guess one."
                        ),
                    },
                    "action": {"type": "string", "enum": list(UPDATE_ACTIONS)},
                    "label": {
                        "type": "string",
                        "description": (
                            "The reminder's current text copied from "
                            "user_state.reminders - for the audit log and undo "
                            "notice."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "edit only: new text, only if changing.",
                    },
                    "due_local": {
                        "type": "string",
                        "description": (
                            "edit only: new LOCAL time, same format as "
                            "set_reminder."
                        ),
                    },
                    "in_minutes": {
                        "type": "integer",
                        "minimum": 1,
                        "description": (
                            "snooze: minutes from now until it comes back "
                            "(default 10). edit: new time relative to now."
                        ),
                    },
                    "shift_minutes": {
                        "type": "integer",
                        "description": (
                            "edit only: move the existing time ('push it back half "
                            "an hour' = 30, 'an hour earlier' = -60)."
                        ),
                    },
                    "recurrence": {
                        **_RECURRENCE_SCHEMA,
                        "description": (
                            "edit only: a new repeat pattern. Leave it out to keep "
                            "the current one."
                        ),
                    },
                },
                "required": ["reminder_id", "action", "label"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        reminder_id = tool_input.get("reminder_id")
        try:
            UUID(str(reminder_id))
        except ValueError:
            return _fail(
                f"{reminder_id!r} is not a reminder id. Use an id exactly as listed "
                "in user_state.reminders, or call get_reminders to find it."
            )

        action = tool_input.get("action")
        if action not in UPDATE_ACTIONS:
            return _fail(f"action must be one of {', '.join(UPDATE_ACTIONS)}.")

        label = tool_input.get("label")
        if not isinstance(label, str) or not label.strip():
            return _fail("label is required - copy the reminder's current text.")

        result: dict[str, Any] = {
            "success": True,
            "queued_for_device": True,
            "reminder_id": str(reminder_id),
            "action": action,
            "label": label.strip(),
        }

        if action == "snooze":
            minutes = tool_input.get("in_minutes", DEFAULT_SNOOZE_MINUTES)
            problem = _check_in_minutes(minutes)
            if problem:
                return _fail(problem)
            result["in_minutes"] = minutes
            return result

        if action != "edit":
            return result

        new_text = tool_input.get("text")
        if new_text is not None and (not isinstance(new_text, str) or not new_text.strip()):
            return _fail("text, if given, must not be empty.")

        timing = [k for k in ("due_local", "in_minutes", "shift_minutes") if _has(tool_input, k)]
        if len(timing) > 1:
            return _fail("Give only one of due_local, in_minutes or shift_minutes.")
        if not new_text and not timing and not _has(tool_input, "recurrence"):
            return _fail("An edit has to change something - the text, the time or the repeat.")

        if "due_local" in timing:
            problem = _check_due_local(tool_input["due_local"], tool_input)
        elif "in_minutes" in timing:
            problem = _check_in_minutes(tool_input["in_minutes"])
        elif "shift_minutes" in timing:
            shift = tool_input["shift_minutes"]
            problem = (
                None if _is_int(shift) and shift != 0 and abs(shift) <= MAX_IN_MINUTES
                else "shift_minutes must be a non-zero whole number of minutes."
            )
        else:
            problem = None
        if problem:
            return _fail(problem)

        problem = _check_recurrence(tool_input.get("recurrence"))
        if problem:
            return _fail(problem)

        for key in ("text", "due_local", "in_minutes", "shift_minutes", "recurrence"):
            if _has(tool_input, key):
                result[key] = tool_input[key]
        return result


class GetRemindersTool(BaseTool):
    """
    Reading the user's reminders over a date range, live from the phone.

    A CLIENT_TOOL (catalogue.py): the Intent Surface intercepts it by name,
    pauses, and resumes with whatever the phone's Room table holds. Reaching
    _execute means that interception broke.
    """

    def __init__(self) -> None:
        super().__init__(
            name="get_reminders",
            description=(
                "Reads the user's reminders live from their device over a date "
                "range. user_state.reminders only covers the next week and "
                "anything that just went off - call this for anything outside "
                "it ('what reminders do I have next month', 'what did I finish "
                "yesterday'), or to find the id of a reminder that isn't "
                "listed there. Times are the user's LOCAL time, ISO 8601 with "
                "no timezone suffix, worked out from the top-level local_time. "
                "Never fabricate reminders you don't have."
            ),
            gain_description=(
                "How readily Nova looks through your reminders without being "
                "asked. For now it only looks when you ask about them."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "from_time": {
                        "type": "string",
                        "description": (
                            "Start of the range in the user's LOCAL time, ISO 8601 "
                            "with no timezone suffix - e.g. 2026-10-05T00:00:00."
                        ),
                    },
                    "to_time": {
                        "type": "string",
                        "description": "End of the range, same format.",
                    },
                    "include_done": {
                        "type": "boolean",
                        "description": (
                            "Also return completed reminders - only when they ask "
                            "what they finished or already did. Default false."
                        ),
                    },
                },
                "required": ["from_time", "to_time"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        return _fail(
            "get_reminders is resolved on the device, not in the backend - it "
            "should have been intercepted as a client tool."
        )
