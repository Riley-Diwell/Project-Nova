"""
Constants used for the controller gain.

Called by controller_gain.py.
"""

from typing import Final

# bound gain from 0 - 1
GAIN_MIN: Final[float] = 0.0
GAIN_MAX: Final[float] = 1.0

# gain starts low (R5)
DEFAULT_GAIN: Final[float] = 0.2


# firing proactively is a function of state confidence and controller gain.
FIRING_THRESHOLD: Final[float] = 0.5


# reinforcement - how much does an accepted / rejected proactive action move the gain
# can be tuned after from testing, small and symmetric for now
REINFORCEMENT_STEP: Final[float] = 0.05

# bound user over riding gain from 0 - 1
OVERRIDE_MIN: Final[float] = GAIN_MIN
OVERRIDE_MAX: Final[float] = GAIN_MAX


def clamp(value: float, low: float = GAIN_MIN, high: float = GAIN_MAX) -> float:
    """
    Clamp a gain value into [low, high].
    """
    return max(low, min(high, value))