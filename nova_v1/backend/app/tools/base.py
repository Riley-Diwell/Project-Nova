"""
Base class for NOVA tools.

Every Function tool subclasses BaseTool and implements _execute(), which holds
the logic for that Function:

class NotesTool(BaseTool):
    def _execute(self, tool_input):
        return result

BaseTool provides what all Function tools share:
- a name
- a description
- an input schema
- invoke(), which runs the tool
- error(), which measures how far off the user is on this tool's own terms

Gain is not part of BaseTool. Gains are attached by registry.py, which pairs each
Function tool with its ControllerGain, so an ordinary callable tool stays
separate from one the Controller may fire unasked.

THE ERROR TERM
error() is the one method the closed control loop needs from a tool, and it is
here rather than in the Controller on purpose: how far off the user is depends
entirely on what the tool does. Only navigation knows that the thing to measure
is slack against a departure time; only the notification tool knows what a
backlog feels like. A generic controller computing "divergence" for tools it
knows nothing about would be computing nothing.

A tool that cannot measure its own divergence returns None and is honestly
excluded from the closed loop, rather than reporting a made-up number. That is
the default, so a tool says nothing about the loop unless it means to.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional


class BaseTool(ABC):
    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        gain_description: str = "",
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.gain_description = gain_description

    def invoke(self, tool_input: dict[str, Any]) -> Any:
        """
        Runs the tool.
        All tool calls go through invoke() which calls the tool's own _execute() function.
        """
        return self._execute(tool_input)

    def error(self, observation: Any) -> Optional[float]:
        """
        How far the user is from where this tool would want them to be, in [0, 1].

        `observation` is a control.observer.Observation. It is typed `Any` for one
        structural reason, not out of laziness: the Observer imports Mode from
        tools/notification_batcher.py, so a tools module importing Observation would
        close a cycle (control -> tools -> control). The dependency points one way
        and the type annotation is the thing that gives. Every override below
        accesses the Observation through named attributes only, so the shape is
        still checked by the tests that build one.

        0 means nothing is diverging and this tool has no reason to act unasked.
        1 means maximal divergence - as far off as this tool can express.
        Normalisation is per-tool and documented on each override, because a
        minute of lateness and an unread notification are not the same units and
        pretending they are is how a generic threshold stops meaning anything.

        None declares the tool **open-loop**: no sensor measures the need for it,
        so it runs on a classified command and nothing else. That is the default
        here - a tool opts into the closed loop by overriding this, and a tool
        that stays quiet about it is correctly treated as reactive.

        Must be a pure function of the Observation: no clock, no network, no
        model. The Observation already carries a reading of "now" taken once for
        the whole turn, and an error term that read its own would make the same
        Episode replay differently (stories 20, 25, 30).
        """
        return None

    @abstractmethod
    def _execute(self, tool_input: dict[str, Any]) -> Any:
        """
        TODO: Implement function here.
        """
        raise NotImplementedError
