"""
Interfaces with the notification batcher (notification_batcher.py).

- Gain should be moderate — Nova can proactively surface a summary
  when context suggests a break (e.g. leaving lecture mode)

  HOW IT CONNECTS TO THE INTENT SURFACE:

  1. REACTIVE:
     Phrases like "what notifications do I have", "snooze notifications",
     "clear everything", "what's waiting" should map to this tool.

     dispatcher.dispatch_reactive("notification_management", {
         "action": "query"
     })

     dispatcher.dispatch_reactive("notification_management", {
         "action": "snooze",
         "snooze_minutes": 30
     })

     dispatcher.dispatch_reactive("notification_management", {
         "action": "acknowledge_all"
     })

  2. PROACTIVE:
     Good triggers
       - current_events contains "lecture" or "tutorial" ending soon
       - location changed (user just left campus)
       - time is a known break point (end of a class hour)

     There is no separate propose/confirm step any more (see
     control/controller.py) - the Controller decides, before the model runs,
     whether this turn's gain x error x confidence clears FIRING_THRESHOLD. If
     it does, "notification_management" is simply among the tools the model is
     offered this turn and it calls it like any other; if not, the tool is
     absent and calling it is not something the model can even attempt. Once
     the user's outcome is known, reinforcer.reinforce("notification_management",
     Outcome.ACCEPTED | REJECTED) moves the learned gain for next time.

THE THREE ACTIONS (subfunctions)
  "query"
      Returns everything currently deliverable given the mode.
      In focus/lecture mode, only high-urgency items are deliverable.
      In default mode, everything is.
      Output includes: count, list of notifications with source/summary/urgency,
      and a spoken string ready to be sent to TTS.

      Example spoken output:
        "3 notifications waiting: person: are you coming?;
         Battery at 15%; Team meeting starting in 10 minutes"

  "snooze"  (+ snooze_minutes, default 30)
      Temporarily extends the batch window so nothing fires for N minutes.
      Useful when the user wants to keep working without being interrupted
      even at a natural break point.

      Example spoken output:
        "Notifications snoozed for 30 minutes."

  "acknowledge_all"
      Marks everything in the current batch as seen and clears the queue.
      Call this after the user has heard or read the batch — otherwise the
      same notifications keep showing up.

      Example spoken output:
        "Cleared 3 notifications."

WHAT IT RETURNS
Every action returns a dict with at least:
  {
      "success": bool,
      "spoken":  str,   send this to TTS / BLE back to the device
      ...action-specific fields...
  }

For "query" specifically:
  {
      "success": True,
      "count":   3,
      "batch": [
          {"source": "messages", "summary": "person: are you coming?", "urgency": "low"},
          {"source": "system",   "summary": "Battery at 15%",  "urgency": "high"},
          {"source": "calendar", "summary": "Meeting in 10 min",  "urgency": "high"},
      ],
      "spoken": "3 notifications waiting: ..."
  }

  STARTUP
The tool shares the notification batcher instance with main.py, which
creates and starts one FastAPI startup:

    from app.tools.functions.notification_management import register_batcher
    from app.tools.functions.notification_batcher import NotificationBatcher

    batcher = NotificationBatcher()
    batcher.start()
    register_batcher(batcher)

If that isn't called, the tool has no batcher to query and 'query' reports
the notification system as not running.

URGENCY LEVELS
  critical  — delivered immediately regardless of mode (emergencies, battery dying)
  high    — delivered at mode boundary or if user checks in
  low   — held until batch window or user explicitly asks
  ambient   — never surfaced proactively (social media, newsletters)

NOTE ON THE PRODUCER SIDE (found during the V2 cleanup pass): nothing in this
codebase — backend or Android — currently ever calls NotificationBatcher's
add_notification(). There is no urgency_classifier.py (mentioned below,
aspirational) and no Android NotificationListenerService feeding events in.
That means 'query'/'snooze'/'acknowledge_all' are reachable and correct, but
in practice always operate on an empty queue until a producer exists. Kept as
honest incomplete infrastructure rather than deleted or faked - see
notification_batcher.py and nova_v2/server/README.md.

The urgency_classifier.py uses the context to decide which level
each incoming notification gets assigned. The intent surface context directly shapes what Nova treats
as urgent vs ignorable.
"""

import threading
from typing import Any
from uuid import UUID

from app.core.request_user import request_user_id
from app.tools.core.base import BaseTool
from app.tools.functions.notification_batcher import NotificationBatcher


class NotificationManagementTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="notification_management",
            description=(
                "Query, snooze, or acknowledge the user's pending notifications. "
                "Call when the user asks what notifications they have, wants to "
                "snooze alerts for a period, or wants to clear their queue. "
                "Also fires proactively when Nova detects a natural break point "
                "such as leaving a lecture or focus mode."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["query", "snooze", "acknowledge_all"],
                        "description": (
                            "What to do: 'query' = list what's waiting, "
                            "'snooze' = hold everything for N minutes, "
                            "'acknowledge_all' = mark everything as seen."
                        ),
                    },
                    "snooze_minutes": {
                        "type": "integer",
                        "description": (
                            "How many minutes to snooze for. Only used when "
                            "action is 'snooze'. Defaults to 30."
                        ),
                    },
                },
                "required": ["action"],
            },
        )

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        action         = tool_input.get("action", "query")
        snooze_minutes = int(tool_input.get("snooze_minutes", 30))

        # The signed-in user this turn is for (core/request_user.py), never
        # anything in tool_input.
        batcher = batcher_for(request_user_id())

        if action == "query":
            return _query(batcher)

        if action == "snooze":
            return _snooze(batcher, snooze_minutes)

        if action == "acknowledge_all":
            return _acknowledge_all(batcher)

        return {
            "success": False,
            "spoken":  "I didn't understand that notification action.",
        }


# One batcher per signed-in user: each has
# its own queue, snooze and mode, so one user's "snooze for an hour" or lecture
# mode never holds back someone else's. Made on a user's first turn, while
# main.py has the batchers running (start_batchers/stop_batchers).
_batchers: dict[str, NotificationBatcher] = {}
_batchers_running = False
_batchers_lock = threading.Lock()


def start_batchers() -> None:
    """main.py, at start-up."""
    global _batchers_running
    _batchers_running = True


def stop_batchers() -> None:
    """main.py, at shutdown."""
    global _batchers_running
    with _batchers_lock:
        _batchers_running = False
        for batcher in _batchers.values():
            batcher.stop()
        _batchers.clear()


def batcher_for(user_id: UUID | str | None) -> NotificationBatcher | None:
    """This user's batcher, made on first use. None with no user, or before
    main.py has started them (e.g. NOVA_MOCK_LLM local runs, tests)."""
    if user_id is None or not _batchers_running:
        return None
    key = str(user_id)
    with _batchers_lock:
        batcher = _batchers.get(key)
        if batcher is None and _batchers_running:
            batcher = _batchers[key] = NotificationBatcher()
            batcher.start()
        return batcher


def set_batcher_mode(user_id: UUID | str, mode: str) -> None:
    """Called by intent_surface.run() every turn with the Observer's derived
    mode, so the batcher stops needing its own copy of calendar_ctx/dnd."""
    batcher = batcher_for(user_id)
    if batcher is not None:
        batcher.set_mode(mode)


#Action handler

def _query(batcher: NotificationBatcher | None) -> dict:
    if batcher is None:
        return {
            "success": True,
            "count":   0,
            "spoken":  "Notification system is not running.",
        }
    batch   = batcher.get_pending_batch()
    summary = batcher.get_summary()
    count   = summary.get("total", 0)

    if count == 0:
        spoken = "You're all clear — nothing waiting."
    elif count == 1:
        spoken = f"One notification: {batch[0].summary}"
    else:
        previews = "; ".join(n.summary for n in batch[:3])
        spoken   = f"{count} notifications waiting: {previews}"
        if count > 3:
            spoken += f" and {count - 3} more"

    return {
        "success":  True,
        "count":    count,
        "batch":    [{"source": n.source, "summary": n.summary,
                      "urgency": n.urgency.value} for n in batch],
        "spoken":   spoken,
    }


def _snooze(batcher: NotificationBatcher | None, minutes: int) -> dict:
    if batcher:
        batcher.snooze(minutes)
    spoken = f"Notifications snoozed for {minutes} minutes."
    return {"success": True, "snoozed_minutes": minutes, "spoken": spoken}


def _acknowledge_all(batcher: NotificationBatcher | None) -> dict:
    if batcher is None:
        return {"success": True, "spoken": "Nothing to clear."}
    batch = batcher.get_pending_batch()
    if batch:
        batcher.acknowledge(batch)
        spoken = f"Cleared {len(batch)} notification{'s' if len(batch) != 1 else ''}."
    else:
        spoken = "Nothing to clear."
    return {"success": True, "cleared": len(batch) if batch else 0, "spoken": spoken}
