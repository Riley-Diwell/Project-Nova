"""
Stores the gain value for one tool.

Each tool has learned gain value and an optional user override.
If an overide exists it is used instead of the learned value.

Called by dispatcher.py and reinforcement.py.
"""

from dataclasses import dataclass
from typing import Optional

from .config import DEFAULT_GAIN, clamp


@dataclass
class ControllerGain:
    # tool's unique name
    name: str

    # learned gain value
    value: float = DEFAULT_GAIN

    # user-set gain (None means no overide)
    override: Optional[float] = None

    # clamp gain values to allowed range 0 - 1
    def __post_init__(self) -> None:
        self.value = clamp(self.value)
        if self.override is not None:
            self.override = clamp(self.override)


    def get_effective(self) -> float:
        """
        Effective gain is the learned gain if override is None, else
        its the override gain. Called by dispatcher.py.
        """
        return self.override if self.override is not None else self.value

    def set_override(self, new_value: float) -> None:
        """
        Set user override
        """
        self.override = clamp(new_value)

    def clear_override(self) -> None:
        """
        Clear the override - revert to using learned gain.
        """
        self.override = None

    def adjust(self, delta: float) -> float:
        """
        Adjust the learned gain by delta.
        Called by reinforcement.py.
        Returns the new learned value.
        """
        self.value = clamp(self.value + delta)
        return self.value

    def is_overridden(self) -> bool:
        """
        Return True if a user override exists.
        """
        return self.override is not None