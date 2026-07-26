"""
Updates a Function tool's gain based on user feedback.

When Nova takes a proactive action:
- accepted by the user -> increase gain
- rejected by the user -> decrease gain

Over time, Function tools learn whether they should act proactively or not.

Only proactive actions should be reinforced. A reactive action does not provide feedback
about whether Nova should act automatically.




gain_store.py isn't implemented yet. Once it exists, the intended wiring
is: call gain_store.save(gain) after adjust() here, so a reinforcement
step persists across restarts — see registry.py for the matching
load-side TODO. Not wired in yet.
"""

from enum import Enum

from ..tools.registry import ToolRegistry
from .config import REINFORCEMENT_STEP


class Outcome(Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


# How much to change the gain for each outcome.
#
# Accepted:
#     increase gain
#
# Rejected:
#     decrease gain
_DELTA: dict[Outcome, float] = {
    Outcome.ACCEPTED: REINFORCEMENT_STEP,
    Outcome.REJECTED: -REINFORCEMENT_STEP,
}


class Reinforcer:
    """ 
    Changes tool gains based on user feedback.
    """
    def __init__(self, registry: ToolRegistry) -> None:
        # use the registry to find tools and their gains.
        self.registry = registry

    def reinforce(self, name: str, outcome: Outcome) -> float:
        """
        Update a tool's gain based on user feedback.

        Accepted:
            gain increases

        Rejected:
            gain decreases
    
        Returns the new learned value (post-clamp) - useful
        for logging "gain moved from X to Y" once Memory exists.

        Raises KeyError if 'name' isn't a registered Function tool.
        """
        self._require_registered(name)
        gain = self.registry.get_gain(name)
        return gain.adjust(_DELTA[outcome])

    def _require_registered(self, name: str) -> None:
        """
        Make sure this tool has a gain.

        Only registered Function tools
        can learn from feedback.
        """
        if not self.registry.has(name):
            raise KeyError(
                f"'{name}' is not a registered Function tool. "
            )