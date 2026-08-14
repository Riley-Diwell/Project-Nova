"""tools/catalogue.py - the Function Tools NOVA has.

WHAT THIS FILE IS
The one list of registered Function tools, and which of them resolve on the
phone. It used to be five register() calls at import time in
intent_surface/loop.py, which meant nothing could ask "what tools exist?" without
importing the Anthropic client and reading an API key.

registry.py stays generic: it is the container, and knows nothing about
navigation or calendars. This module is the wiring.
"""
from __future__ import annotations

from typing import Optional

from .calendar_tool import (
    AddCalendarEventTool,
    CalendarTool,
    DeleteCalendarEventTool,
    EditCalendarEventTool,
)
from .memory_tool import MemoryTool
from .navigation import NavigationTool
from .notification_management import NotificationManagementTool
from .registry import ToolRegistry

try:
    from ..gain.gain_store import GainStore        # app.tools.catalogue
except ImportError:  # pragma: no cover
    from gain.gain_store import GainStore          # tools.catalogue (cwd = backend/app)

# Tools that run on the phone rather than here, and whose answer the model needs
# before it can speak - so the Intent Surface pauses the conversation, hands the
# call to the device, and resumes with what comes back (loop.py's NeedMoreResult).
#
# add_calendar_event, edit_calendar_event and delete_calendar_event also run on
# the phone but are NOT here: nothing has to come back, so there is nothing to
# wait for. Their Action is the instruction (delete's is gated behind an
# on-device confirm dialog before it takes effect, but that gate is Android's,
# not a hop back to here).
CLIENT_TOOLS: frozenset[str] = frozenset({"get_calendar_range"})


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
    return registry
