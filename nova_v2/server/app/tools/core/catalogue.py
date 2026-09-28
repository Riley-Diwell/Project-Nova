"""tools/core/catalogue.py - the Function Tools NOVA has.

WHAT THIS FILE IS
The one list of registered Function tools, and which of them resolve on the
phone. It used to be five register() calls at import time in
intent_surface.py, which meant nothing could ask "what tools exist?" without
importing the Anthropic client and reading an API key.

registry.py stays generic: it is the container, and knows nothing about
navigation or calendars. This module is the wiring.
"""
from __future__ import annotations

from typing import Optional

from app.control.gain.gain_store import GainStore
from app.tools.core.registry import ToolRegistry
from app.tools.functions.alarm_tool import SetAlarmTool, SetTimerTool
from app.tools.functions.calendar_tool import (
    AddCalendarEventTool,
    CalendarTool,
    DeleteCalendarEventTool,
    EditCalendarEventTool,
)
from app.tools.functions.memory_tool import MemoryTool
from app.tools.functions.navigation import NavigationTool
from app.tools.functions.notification_management import NotificationManagementTool
from app.tools.functions.reminder_tool import (
    GetRemindersTool,
    SetReminderTool,
    UpdateReminderTool,
)

# Tools that run on the phone rather than here, and whose answer the model needs
# before it can speak - so the Intent Surface pauses the conversation, hands the
# call to the device, and resumes with what comes back (intent_surface.py's
# NeedMoreResult).
#
# add_calendar_event, edit_calendar_event and delete_calendar_event also run on
# the phone but are NOT here: nothing has to come back, so there is nothing to
# wait for. Their Action is the instruction (delete's is gated behind an
# on-device confirm dialog before it takes effect, but that gate is Android's,
# not a hop back to here).
#
# get_reminders is the same shape as get_calendar_range: the reminders live in
# the phone's own table (reminder_tool.py), so a range outside
# user_state.reminders has to be read there.
CLIENT_TOOLS: frozenset[str] = frozenset({"get_calendar_range", "get_reminders"})

# Fire-and-forget tools that validate their own input. When one returns
# {"success": False} its Action is recorded with ran=False, so the phone - which
# skips ran=false Actions - never carries out an instruction the tool rejected.
# The calendar and alarm tools are not here because they accept whatever they
# are given; this is only for tools with something to reject.
DEVICE_TOOLS: frozenset[str] = frozenset({"set_reminder", "update_reminder"})


def build_registry(gain_store: Optional[GainStore] = None) -> ToolRegistry:
    """A registry holding every Function tool, each with its saved gain.

    The gain store is passed in rather than constructed so each tool comes up
    where it was left rather than back at DEFAULT_GAIN - without it the dial in
    the Android app's Gain tab would reset on every restart - and so a test can
    hand in a store pointed at a tmpdir.

    Context tools (get_current_address) are deliberately absent: they carry no
    gain, are never gated, and so have nothing to be registered for.
    """
    registry = ToolRegistry(gain_store=gain_store)
    registry.register(NavigationTool())
    registry.register(NotificationManagementTool())
    registry.register(MemoryTool())
    # All four execute on the phone. They are registered anyway, because
    # registration is what gives a tool a controller gain and therefore a dial:
    # how readily NOVA looks ahead in your calendar, schedules a plan you
    # merely mentioned, applies a restated change, or offers to remove
    # something, is worth tuning even though the write happens off-box.
    registry.register(CalendarTool())
    registry.register(AddCalendarEventTool())
    registry.register(EditCalendarEventTool())
    registry.register(DeleteCalendarEventTool())
    # Same fire-and-forget shape as the calendar writes above, just handed to
    # the Clock app instead of the Calendar Provider - see alarm_tool.py.
    registry.register(SetTimerTool())
    registry.register(SetAlarmTool())
    # Reminders Nova delivers itself - stored and fired on the phone, so the
    # same fire-and-forget Actions plus a client tool for reading a range.
    registry.register(SetReminderTool())
    registry.register(UpdateReminderTool())
    registry.register(GetRemindersTool())
    return registry
