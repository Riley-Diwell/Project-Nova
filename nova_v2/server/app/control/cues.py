"""control/cues.py - did the user just state something they need to do?

WHAT THIS FILE IS
A deterministic reading of one Event's words, the sibling of commands.py and
built the same way. commands.py answers "did the user ask for something?";
this answers "did the user say they have to do something?" - which is the
divergence set_reminder measures (reminder_tool.SetReminderTool.error).

"I need to submit the form by 5" is not a command, so commands.py rightly
leaves it alone and the Controller consults gain. Without a measurement here
the reminder tool would be open-loop and the dial would do nothing; with it,
gain decides whether that sentence becomes a reminder.

WHAT THE RULE IS
  obligation + time   1.0   "I need to email Dr Chen by 5"
  obligation alone    0.5   "I have to call mum"
  anything else       0.0

An obligation is one of a short list of phrases. A time is a clock time, a
relative time, a named day or part of day, or an anchor like "after class".
Numbers are matched as digits and as words, because on-device speech-to-text
writes "at five".

ON BEING WRONG
Like commands.py it misreads some sentences, and it misreads the same ones
every time. tests/test_cues.py pins the known misreadings as tests - "I have
to say, that was good" reads as an obligation - so a fix is a code change
rather than folklore. The Controller multiplies this by gain and prediction
confidence, so a misreading only acts at a gain the user chose.
"""
from __future__ import annotations

import re
from typing import Any

from app.control.commands import SPEECH_EVENT_TYPES, _strip_address

STRONG = 1.0
WEAK = 0.5

_OBLIGATION = re.compile(
    r"\b(?:"
    r"i need to|i have to|i(?:'ve| have)? got to|i(?:'ve)? gotta|ive got to|ive gotta"
    r"|i must|i should|i(?:'m| am)? supposed to|im supposed to"
    r"|(?:i )?(?:can't|cant|cannot|mustn't|mustnt) forget|remember to"
    r")\b"
)

_NUMBER = (
    r"(?:\d{1,2}(?::\d{2})?"
    r"|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
    r"|noon|midday|midnight)"
)

_WEEKDAYS = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"

_TIME = re.compile(
    r"\b(?:"
    r"today|tonight|tomorrow|later|soon|this (?:arvo|afternoon|evening|morning|weekend)"
    rf"|{_WEEKDAYS}|next week|end of (?:the )?(?:day|week)"
    rf"|(?:at|by|before|until|till) {_NUMBER}"
    r"|\d{1,2}(?::\d{2})? ?(?:am|pm)|o'?clock"
    r"|in (?:\d+|a|an|one|two|three|four|five|ten|twenty|thirty|half an?) "
    r"(?:min|mins|minute|minutes|hour|hours|hr|hrs|day|days)"
    r"|(?:after|before) (?:class|the lecture|this lecture|the class|lunch|dinner|work|uni|school|the meeting|this)"
    r")\b"
)


def obligation_cue(event: Any) -> float:
    """1.0, 0.5 or 0.0 - see the module docstring. Pure: the same Event always
    scores the same."""
    if getattr(event, "type", None) not in SPEECH_EVENT_TYPES:
        return 0.0
    spoken = getattr(event, "text", None)
    if not isinstance(spoken, str) or not spoken.strip():
        return 0.0

    text = _strip_address(spoken.strip().lower()).replace("’", "'")
    if not _OBLIGATION.search(text):
        return 0.0
    return STRONG if _TIME.search(text) else WEAK
