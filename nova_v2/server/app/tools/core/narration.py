"""tools/core/narration.py - plain-English text for the audit log.

WHAT THIS FILE IS
GET /audit (main.py) hands the Android app raw tool names and JSON `input` -
meaningless to a user who isn't reading this source. This module is the one
place that turns a (tool, input) pair or a triggering Event into a sentence.

Deliberately NOT a method on each BaseTool. A tool's _execute() and its gain's
error() are the real contract (see tools/core/base.py); this is presentation
only, for one screen, and centralising it here means one file to extend when
a new tool ships, rather than six.

FORWARD COMPATIBILITY
describe_action() falls back to "Ran {tool}." for anything it doesn't
recognise, and describe_event() falls back to None - so a new tool or event
type added later shows something honest immediately, not a crash or a
mysteriously blank line, and gets a real sentence whenever this file is next
touched.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def describe_event(event_type: str, event: dict[str, Any]) -> str | None:
    """What triggered the episode, in plain English - schemas/event.py's Event
    union. For a `voice` event this is the transcript verbatim, which IS what
    the user asked; for everything else it is what Nova noticed on its own.
    None for an event with nothing worth showing (a bare periodic tick).
    """
    if event_type == "voice":
        text = event.get("text")
        return text if isinstance(text, str) and text.strip() else None

    if event_type == "notification":
        app = event.get("app") or "App"
        title = event.get("title")
        return f"{app} notification: {title}" if title else f"{app} notification"

    if event_type == "calendar_trigger":
        name = event.get("calendar_event_name")
        return f"Calendar changed: {name}" if name else "Calendar changed"

    if event_type == "accelerometer":
        threshold = event.get("threshold")
        return f"Activity changed to {threshold}" if threshold else None

    if event_type == "screen":
        return "Screen turned on" if event.get("status") else "Screen turned off"

    if event_type == "note_captured":
        # Content-free by design (api/notes.py _log_captured) - the sentence
        # can say what kind of note and where, never what it said.
        kind = {"capture": "a recording", "dictation": "a dictated note"}.get(
            event.get("kind"), "a voice note")
        duration = event.get("duration_s")
        if isinstance(duration, (int, float)) and duration >= 60:
            kind = f"{kind} ({round(duration / 60)} min)"
        where = event.get("calendar_title")
        return f"Captured {kind} during {where}" if where else f"Captured {kind}"

    # "timestamp" (a bare periodic tick) and "note" (memory_tool's own episodic
    # bookkeeping row, never closed with an action - see memory_tool.py) have
    # nothing a user would recognise as a trigger.
    return None


def _format_time(dt: datetime) -> str:
    """"10:00am" - lowercase am/pm, no leading zero. Deliberately not %-I/%-d
    (glibc-only strftime flags that are absent on some platforms) - built by
    hand so this behaves the same wherever it runs."""
    hour12 = dt.hour % 12 or 12
    ampm = "am" if dt.hour < 12 else "pm"
    return f"{hour12}:{dt.minute:02d}{ampm}"


def _format_local_dt(value: str | None) -> str | None:
    """"Fri, Sep 18, 10:00am" from one of the tool schemas' LOCAL-time ISO
    strings (calendar_tool.py: "no timezone suffix... the phone reads this in
    its own timezone"). Returns the raw string unparsed rather than hiding it
    if it doesn't parse - an odd value is still worth showing.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    return f"{dt.strftime('%a, %b')} {dt.day}, {_format_time(dt)}"


def _format_local_range(start: str | None, end: str | None) -> str | None:
    """Both ends of a calendar range, readably - the end time only repeats the
    date if it falls on a different day than the start."""
    if not start and not end:
        return None
    if not (start and end):
        return _format_local_dt(start or end)

    start_fmt, end_fmt = _format_local_dt(start), _format_local_dt(end)
    try:
        if datetime.fromisoformat(start).date() == datetime.fromisoformat(end).date():
            end_fmt = _format_time(datetime.fromisoformat(end))
    except ValueError:
        pass
    return f"{start_fmt} – {end_fmt}"


def describe_action(tool: str, tool_input: dict[str, Any], ran: bool) -> str:
    """One sentence for one tool call, built from that tool's actual
    input_schema (tools/functions/*.py) - not guessed."""
    if tool == "canvas":
        return _describe_canvas(tool_input, ran)

    if tool == "add_calendar_event":
        title = tool_input.get("title", "an event")
        if not ran:
            return f"Considered adding '{title}' to your calendar, but didn't."
        when = _format_local_range(tool_input.get("start_time"), tool_input.get("end_time"))
        return f"Added '{title}' to your calendar ({when})." if when else f"Added '{title}' to your calendar."

    if tool == "edit_calendar_event":
        if not ran:
            return "Considered changing a calendar event, but didn't."
        title = tool_input.get("title")
        when = _format_local_range(tool_input.get("start_time"), tool_input.get("end_time"))
        base = f"Updated '{title}' on your calendar" if title else "Updated your calendar event"
        return f"{base} ({when})." if when else f"{base}."

    if tool == "delete_calendar_event":
        title = tool_input.get("title", "an event")
        if not ran:
            return f"Considered removing '{title}' from your calendar, but didn't."
        return f"Asked you to confirm deleting '{title}'."

    if tool == "get_calendar_range":
        when = _format_local_range(tool_input.get("from_time"), tool_input.get("to_time"))
        return f"Checked your calendar for {when}." if when else "Checked your calendar."

    if tool == "navigation_departure_time":
        destination = tool_input.get("destination", "your destination")
        arrival = tool_input.get("arrival_time")
        if not ran:
            return f"Considered checking directions to {destination}, but didn't."
        return (
            f"Checked directions to {destination}, arriving by {arrival}."
            if arrival else f"Checked directions to {destination}."
        )

    if tool == "memory":
        action = tool_input.get("action")
        if action == "save":
            return _describe_memory_save(tool_input)
        if action == "recall":
            query = (tool_input.get("query") or "").strip()
            return (f"Looked up what you've told Nova about \"{query}\"." if query
                    else "Looked up what you've told Nova.")
        return ("Checked what you've told Nova." if ran
                else "Considered checking what you've told Nova, but didn't.")

    if tool == "notification_management":
        action = tool_input.get("action")
        if action == "snooze":
            minutes = tool_input.get("snooze_minutes", 30)
            return f"Snoozed notifications for {minutes} minutes."
        if action == "acknowledge_all":
            return "Cleared all pending notifications."
        return "Checked pending notifications." if ran else "Considered checking notifications, but didn't."

    if tool == "set_reminder":
        text = (tool_input.get("text") or "").strip() or "something"
        if not ran:
            return f"Considered setting a reminder to '{text}', but didn't."
        return f"Set a reminder to '{text}'{_reminder_when(tool_input)}."

    if tool == "update_reminder":
        label = (tool_input.get("label") or "").strip() or "a reminder"
        action = tool_input.get("action")
        if not ran:
            verb = {"complete": "marking", "snooze": "snoozing", "edit": "changing",
                    "delete": "removing"}.get(action, "changing")
            return f"Considered {verb} the reminder '{label}', but didn't."
        if action == "complete":
            return f"Marked '{label}' done."
        if action == "snooze":
            minutes = tool_input.get("in_minutes") or 10
            return f"Snoozed '{label}' for {_minutes_phrase(minutes)}."
        if action == "delete":
            return f"Removed the reminder '{label}'."
        return f"Changed the reminder '{label}'."

    if tool == "get_reminders":
        when = _format_local_range(tool_input.get("from_time"), tool_input.get("to_time"))
        return f"Checked your reminders for {when}." if when else "Checked your reminders."

    return f"Ran {tool}." if ran else f"Considered running {tool}, but didn't."


def _describe_memory_save(tool_input: dict[str, Any]) -> str:
    """A memory save, by where it landed. Filed under a category, it went into
    long-term memory (Persona - the Knowledge Map); otherwise it became a note
    in the Notes tab, which is a different thing and is called one.

    saved_as/outcome are recorded from the tool's result (intent_surface.py,
    _memory_outcome). Episodes from before that fall back to the category,
    which is what decided it."""
    text = (tool_input.get("text") or "").strip()
    quoted = f': "{text}".' if text else "."
    saved_as = tool_input.get("saved_as")
    if saved_as is None and "failed" not in tool_input:
        saved_as = "memory" if tool_input.get("category") else "note"
    if saved_as == "memory":
        outcome = tool_input.get("outcome")
        if outcome == "merged":
            return f"Already remembered this, so nothing changed{quoted}"
        if outcome == "replaced":
            return f"Updated a memory{quoted}"
        return f"Remembered{quoted}"
    if saved_as == "note":
        return f"Saved a note{quoted}"
    return f"Tried to remember this, but couldn't save it{quoted}"


def _describe_canvas(tool_input: dict[str, Any], ran: bool) -> str:
    """A canvas call - telling a document Nova opened and read apart from a
    lookup of Canvas's own data (what's due, marks, course calendar)."""
    action = tool_input.get("action")
    course = tool_input.get("course_name") or tool_input.get("course")
    in_course = f" in {course}" if course else ""

    if action == "read":
        document = tool_input.get("document")
        if ran and document:
            kind = {"File": "document", "Page": "page", "Assignment": "assignment brief",
                    "Quiz": "quiz description"}.get(tool_input.get("document_type") or "", "document")
            return f"Read the {kind} '{document}'{in_course} on Canvas."
        item = tool_input.get("item") or "a document"
        return f"Tried to read '{item}'{in_course} on Canvas, but couldn't open it."

    if action == "upcoming":
        days = tool_input.get("days")
        span = f" in the next {days} days" if days else ""
        what = f"Checked Canvas for what's due{in_course}{span}."
    elif action == "grades":
        assignment = tool_input.get("assignment")
        what = (f"Checked your mark for '{assignment}'{in_course} on Canvas." if assignment
                else f"Checked your grades{in_course} on Canvas.")
    elif action == "classes":
        what = f"Checked the Canvas calendar{' for ' + course if course else ''}."
    elif action == "materials":
        query = tool_input.get("query")
        what = (f"Looked through the course materials{in_course} on Canvas"
                + (f" for '{query}'." if query else "."))
    else:
        what = "Checked Canvas."
    if ran:
        return what
    # "Checked X." -> "Tried to check X, but Canvas didn't answer."
    verb, rest = what.split(" ", 1)
    base = {"Checked": "check", "Looked": "look"}.get(verb, verb.lower())
    return f"Tried to {base} {rest[:-1]}, but Canvas didn't answer."


def _minutes_phrase(minutes: Any) -> str:
    """"20 minutes", "1 hour", "1 hour 30 minutes" - for in_minutes values."""
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
        return f"{minutes} minutes"
    hours, mins = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if mins:
        parts.append(f"{mins} minute" + ("s" if mins != 1 else ""))
    return " ".join(parts)


def _reminder_when(tool_input: dict[str, Any]) -> str:
    """" for Wed, Sep 23, 4:30pm" / " in 20 minutes" / "" - set_reminder's time,
    plus how it repeats if it does."""
    due = _format_local_dt(tool_input.get("due_local"))
    minutes = tool_input.get("in_minutes")
    when = f" for {due}" if due else (f" in {_minutes_phrase(minutes)}" if minutes else "")
    recurrence = tool_input.get("recurrence")
    if isinstance(recurrence, dict) and recurrence.get("frequency"):
        when += f", repeating {recurrence['frequency']}"
    return when


def matches_query(entry: dict[str, Any], q: str) -> bool:
    """Case-insensitive substring match across every field a user might
    search by - used for the Audit tab's search box."""
    needle = q.strip().lower()
    if not needle:
        return True
    haystacks = (entry.get("summary"), entry.get("context"), entry.get("speech"),
                 entry.get("reason"), entry.get("tool"))
    return any(isinstance(h, str) and needle in h.lower() for h in haystacks)
