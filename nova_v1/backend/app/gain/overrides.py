"""
Applies the user's gain override to a Function tool.

Sibling to reinforcement.py, and deliberately the same shape:

    Reinforcer      moves the *learned* value from proactive accept/reject
    GainOverrides   sets the *override* the user dialled in

Both take (registry, gain_store), mutate the ControllerGain the registry holds,
and persist it. Both refuse tools that aren't registered Function tools, since
only those have a gain at all - context tools (lookups) never fire proactively
and so have nothing to tune.

In V1 gain is entirely user-set (backend/db/schema.sql), which makes this the
path that actually decides whether a tool fires: the Android app's Gain tab
(ui/screens/GainScreen.kt) calls it through GET/PUT /tools/gain in main.py.
"""

from typing import Any, Optional

from .controller_gain import ControllerGain
from .gain_store import GainStore

try:
    from ..tools.registry import ToolRegistry
except ImportError:  # pragma: no cover
    from tools.registry import ToolRegistry


class GainOverrides:
    """
    Reads and sets user gain overrides for registered Function tools.
    """
    def __init__(self, registry: ToolRegistry, gain_store: Optional[GainStore] = None) -> None:
        self.registry = registry
        # optional - if set, set() persists the updated gain here
        self.gain_store = gain_store

    def set(self, name: str, override: Optional[float]) -> dict[str, Any]:
        """
        Set a tool's user override, or clear it with None so the tool goes back
        to its learned value.

        Returns the tool's gain as it now stands, so the caller renders what
        actually landed rather than what it asked for. ControllerGain clamps to
        [0, 1], so an out-of-range value is bounded rather than rejected.

        Raises KeyError if 'name' isn't a registered Function tool.
        """
        self._require_registered(name)
        gain = self.registry.get_gain(name)

        if override is None:
            gain.clear_override()
        else:
            gain.set_override(override)

        if self.gain_store is not None:
            self.gain_store.save(gain)

        print(f"[gain] {name} override={gain.override} effective={gain.get_effective()}")
        return self.view(name)

    def view(self, name: str) -> dict[str, Any]:
        """
        One tool's gain as the wire sees it (schemas/tool_gain.py).

        Both numbers go out, not just the effective one: `value` is what
        reinforcement has learned and `override` is what the user dialled in, so
        the Gain tab can show the learned value behind the dial and make clear
        what resetting would hand back to.
        """
        gain: ControllerGain = self.registry.get_gain(name)
        tool = self.registry.get_tool(name)
        return {
            "name": name,
            "description": tool.gain_description or tool.description,
            "value": gain.value,
            "override": gain.override,
            "effective": gain.get_effective(),
        }

    def view_all(self) -> list[dict[str, Any]]:
        """
        Every registered Function tool with its current gain - one dial each.
        """
        return [self.view(name) for name in self.registry.all_names()]

    def _require_registered(self, name: str) -> None:
        """
        Make sure this tool has a gain.

        Only registered Function tools can be overridden.
        """
        if not self.registry.has(name):
            raise KeyError(
                f"'{name}' is not a registered Function tool. "
            )
