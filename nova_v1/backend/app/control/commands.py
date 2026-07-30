"""control/commands.py - was this a command, or was the user just talking?

WHAT THIS FILE IS
A deterministic, syntactic classifier over an Event. It answers one question:
did the user ask for something this turn?

WHY IT IS NOT THE MODEL'S JOB ANY MORE
It used to be. Every Function tool's input schema carried a required `trigger`
field, and the model filled it in about its own call - "requested" if it judged
the user's words to be a request, "inferred" otherwise. That declaration was the
switch on the whole control loop: `requested` bypassed Controller Gain entirely.

Asking the observer to classify whether its own output was a command or a
correction, and then letting that classification gate the loop, is not a control
system. It also could not be tested: the same sentence classified differently on
different runs, so a misfire could not be reproduced, and every fix was another
paragraph of prose in the schema description asking the model to be stricter.

WHAT THE RULE IS
Exactly what that prose asked for, in code. A question or an imperative is a
command. A bare declarative is not - and in particular a statement does not
become a request because it is useful to act on, because it is addressed to
NOVA, or because a high gain means NOVA is going to act on it anyway. That last
one is the point: "coffee with Sam on Thursday at ten" is the sentence a high
add_calendar_event gain should act on and a zero gain should ignore, and
classifying it as a command would take that choice away from the user.

Ambient events - a notification arriving, a calendar trigger, a screen change -
carry no command by construction, because nobody asked.

WHAT IT DOES NOT DO
It does not say which Tool the user meant. That is irreducibly linguistic and
stays with the model. Whether a command was issued is grammar, and grammar is
arithmetic.

ON BEING WRONG
It will misread some sentences. That is accepted and it is an improvement,
because it misreads the *same* sentences every time, a failing test pins each
one, and a fix is a code change. See tests/test_commands.py, which carries the
known misreadings as tests rather than leaving them as folklore.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

# Only a person speaking can be a request. Everything else in the Event union
# happens *to* the user - a notification arrives, a calendar entry starts, the
# screen wakes - so no amount of usefulness makes it something they asked for.
SPEECH_EVENT_TYPES = frozenset({"voice"})

# Addressing NOVA is not the verb. Stripped first, or every wake-word utterance
# reads as a declarative beginning "hey".
_VOCATIVE = re.compile(r"^\s*(?:ok(?:ay)?\s+|hey\s+|hi\s+)?nova\b[\s,]*", re.I)

# Politeness wrappers, likewise stripped so the verb underneath is visible.
_LEADING_POLITENESS = re.compile(r"^\s*(?:please|pls|um|uh|so|and)\b[\s,]*", re.I)

# Interrogatives. A leading wh-word or auxiliary is a question whether or not
# the transcription bothered with a question mark - and STT usually does not.
_WH_WORDS = frozenset({
    "what", "whats", "when", "whens", "where", "wheres", "who", "whos",
    "which", "why", "how", "hows", "whose", "whom",
})
_AUXILIARIES = frozenset({
    "is", "are", "am", "was", "were", "do", "does", "did", "have", "has",
    "had", "can", "could", "will", "would", "should", "shall", "may",
    "might", "must",
})

# Imperative verbs NOVA's Functions can be asked for. Deliberately a list of
# what this product does rather than an attempt at English: a verb NOVA has no
# Tool for does not need to be recognised, because nothing would run either way.
_IMPERATIVE_VERBS = frozenset({
    # memory
    "remember", "remind", "note", "save", "store", "forget", "recall",
    # retrieval and speech
    "tell", "say", "read", "show", "list", "check", "find", "search", "look",
    "repeat", "summarise", "summarize",
    # calendar
    "add", "schedule", "book", "put", "create", "move", "cancel", "delete",
    "clear", "block",
    # navigation
    "navigate", "take", "get", "drive", "walk", "route", "direct",
    # notifications
    "snooze", "mute", "silence", "dismiss", "acknowledge", "hold", "pause",
    # generic
    "give", "make", "set", "start", "stop", "open", "play", "send", "call",
    "help",
})

# Request frames that are neither a leading wh-word nor a leading imperative.
# "can you x" opens with an auxiliary and is caught anyway; these are the ones
# where the ask sits in the middle of the sentence.
_REQUEST_FRAMES = (
    "i need you to",
    "i want you to",
    "i'd like you to",
    "id like you to",
    "i would like you to",
    "let me know",
    "can you",
    "could you",
    "would you",
    "will you",
)


@dataclass(frozen=True)
class Command:
    """The user asked for something this turn.

    Carries the words and nothing else. In control terms a command is a
    **reference step** - the setpoint moved because the user moved it - which is
    why the Controller runs the Tool the model names regardless of gain: gain
    governs disturbance correction, never a reference change.

    Deliberately does NOT name a Tool. If it did, this module would be back in
    the business of reading intent, which is the model's job.
    """

    text: str


def classify(event: Any) -> Optional[Command]:
    """A Command if the user asked for something, otherwise None.

    Pure: no clock, no network, no model, no store. The same Event always
    classifies the same way, which is what story 6 - "the same words in the same
    situation produce the same behaviour" - actually requires.
    """
    if getattr(event, "type", None) not in SPEECH_EVENT_TYPES:
        return None

    spoken = getattr(event, "text", None)
    if not isinstance(spoken, str) or not spoken.strip():
        return None

    return Command(text=spoken) if _is_request(spoken) else None


def _is_request(spoken: str) -> bool:
    """The rule, in the order the cheap checks come first."""
    text = _strip_address(spoken.strip().lower())
    if not text:
        return False

    if text.endswith("?"):
        return True

    words = re.findall(r"[a-z']+", text)
    if not words:
        return False

    first = words[0].replace("'", "")
    if first in _WH_WORDS or first in _AUXILIARIES or first in _IMPERATIVE_VERBS:
        return True

    # "let's" is an imperative wearing a contraction.
    if words[0] in ("lets", "let's") or (words[0] == "let" and len(words) > 1):
        return True

    return any(frame in text for frame in _REQUEST_FRAMES)


def _strip_address(text: str) -> str:
    """Remove the wake phrase and any leading politeness, repeatedly.

    Repeated because they stack: "hey nova, please remind me..." needs both gone
    before "remind" is the first word.
    """
    for _ in range(4):
        stripped = _LEADING_POLITENESS.sub("", _VOCATIVE.sub("", text)).strip()
        if stripped == text:
            return stripped
        text = stripped
    return text
