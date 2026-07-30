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


# How much authority a Tool needs before it acts unasked. The Controller computes
#
#     authority = gain x error x prediction_confidence
#
# so this is compared against a product of three numbers each in [0, 1], not
# against the gain-times-state-confidence pair it used to face.
#
# WHY IT MOVED FROM 0.5 TO 0.15
# The number had to change when the quantity being compared changed, and 0.5 was
# calibrated for the old one. Two ends to check:
#
#   at DEFAULT_GAIN (0.2)  a Tool needs error x confidence >= 0.75 - severe
#                          divergence, seen clearly - before it acts unasked. At
#                          0.5 the requirement was 2.5, i.e. unreachable, and a
#                          Tool at its default dial could never fire however late
#                          the user was. That unreachability is the whole premise
#                          of ADR-0002's "score every turn" argument, and keeping
#                          0.5 would have preserved the bug the spec set out to
#                          remove (see ADR-0005).
#   at gain 1.0            error x confidence >= 0.15 acts. A user who has dialled
#                          a Function to 1.0 has asked for action on inference,
#                          and DESIGN.md Section 5.7 reads a high gain as an
#                          instruction rather than mere permission.
#
# This and DEFAULT_GAIN are the tuning surface. Changing either changes observable
# behaviour, so tests/test_controller.py pins both ends of the range above.
FIRING_THRESHOLD: Final[float] = 0.15


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