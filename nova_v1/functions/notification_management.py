"""

Interfaces with the notification batcher (notification_batcher.py).

- Gain should be moderate — Nova can proactively surface a summary
  when context suggests a break (e.g. leaving lecture mode)
"""

from typing import Any
from ..base import BaseTool


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

        # Import batcher at call time 
        # and works even if batcher hasn't started yet
        try:
            from notification_batcher import NotificationBatcher
            batcher = _get_batcher()
        except ImportError:
            batcher = None

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


# nova_main.py creates the batcher — this gets a reference to it.
# If running by itself or testing a fresh batcher is created.
_batcher_instance = None

def register_batcher(batcher) -> None:
    """Called by nova_main.py to share its batcher instance."""
    global _batcher_instance
    _batcher_instance = batcher

def _get_batcher():
    global _batcher_instance
    if _batcher_instance is None:
        try:
            from notification_batcher import NotificationBatcher
            _batcher_instance = NotificationBatcher()
            _batcher_instance.start()
        except ImportError:
            pass
    return _batcher_instance


#Action handler

def _query(batcher) -> dict:
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


def _snooze(batcher, minutes: int) -> dict:
    # Set batch window temporarily next delivery after N minutes
    if batcher:
        batcher.BATCH_WINDOW_MINUTES = minutes
    spoken = f"Notifications snoozed for {minutes} minutes."
    return {"success": True, "snoozed_minutes": minutes, "spoken": spoken}


def _acknowledge_all(batcher) -> dict:
    if batcher is None:
        return {"success": True, "spoken": "Nothing to clear."}
    batch = batcher.get_pending_batch()
    if batch:
        batcher.acknowledge(batch)
        spoken = f"Cleared {len(batch)} notification{'s' if len(batch) != 1 else ''}."
    else:
        spoken = "Nothing to clear."
    return {"success": True, "cleared": len(batch) if batch else 0, "spoken": spoken}
