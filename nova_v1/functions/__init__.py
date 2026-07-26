"""
Navigation, Notification Management, Note Taking.

Register with Naoise's ToolRegistry:

    from functions import NavigationTool, NotificationManagementTool, NoteTakingTool

    registry.register(NavigationTool())
    registry.register(NotificationManagementTool())
    registry.register(NoteTakingTool())
"""

from .navigation             import NavigationTool
from .notification_management import NotificationManagementTool
from .note_taking            import NoteTakingTool

__all__ = ["NavigationTool", "NotificationManagementTool", "NoteTakingTool"]
