"""control/ack.py - "Sure, checking your calendar now."

WHAT THIS FILE IS
The line the phone says while a turn is still running, so a slow reply isn't
heard as silence. The phone asks for it alongside /event and speaks it only if
the real reply hasn't landed after a moment; a fast turn never hears it.

WHO GETS ONE
A turn that needs something looked up or done first - "what's on tomorrow",
"how'd I go on the test", "remind me at 5". "I'm tired" or "how are you"
answered with "On it" sounds like a machine, so those get None and the phone
says nothing.

WAKING UP
Until the model server has read NOVA's prompt since a restart (main.py's
keep-warm), every turn takes ~12 s, so every turn - "how are you" too - hears
"Give me a moment, I'm just waking up."

WHO DECIDES
The model: it reads what was said, decides whether anything needs checking,
and names the thing asked about ("Let me check your power systems test
results"). Keyword rules can't keep up with how people ask - "how'd I go on
the testt" names no topic at all. It runs on the background server
(llm.LLM_BG_BASE_URL), which is the voice server until a second GPU is set
up: a ~300-token request beside the turn it covers for, measured at 300-380 ms
on 2026-10-27, against a turn of 4-12 s. Only when it's slower than
MODEL_TIMEOUT_S or fails does a keyword table decide instead.

The line promises only that NOVA is working on it, never what it will find:
it's spoken before the turn has decided anything.
"""
from __future__ import annotations

import os
import random
import re
from typing import Any, Optional

from pydantic import BaseModel

from app.control import commands
from app.core import llm
from app.store import persona

# The background model's leash. The phone speaks the line 0.8 s after asking,
# so a phrasing that takes longer than this has missed its moment anyway.
MODEL_TIMEOUT_S = 1.2
# No model to ask in local pipeline testing (intent_surface.MOCK_LLM).
MOCK_LLM = os.environ.get("NOVA_MOCK_LLM", "").strip().lower() in ("1", "true", "yes")
# Room for a line said in a style: "Arr, give me a moment while I check yer marks!"
MAX_CHARS = 90

PROMPT = (
    "You are NOVA, a friendly voice assistant. The user just said something "
    "and your real reply is still being worked out.\n"
    "If replying means looking something up or doing something - checking "
    "their calendar, reminders, notes, notifications or a route, setting a "
    "reminder, timer or alarm, reading their Canvas coursework, searching the "
    "web - give one short, casual spoken line, three to eight words, saying "
    "what you're doing the way a friend would, and name the thing they asked "
    "about: \"Sure, let me check the weighting for you.\", \"Let me see what's "
    "due next week.\", \"One sec, setting that reminder.\" Don't answer, "
    "don't promise a result, don't invent details.\n"
    "If it's small talk, a greeting, an opinion, something you can just "
    "answer, or they're only telling you something, ack is null - nothing "
    "should be said first."
)


WAKING_PROMPT = (
    "\nRight now you're just starting up, so this reply will be slow. Whatever "
    "they said - small talk included - give a short line asking them to give "
    "you a moment while you wake up."
)
STANDING_PROMPT = (
    "\nThe user has told you how to talk to them. Say the line that way too:"
)


class _Ack(BaseModel):
    ack: Optional[str] = None


# name -> (words that mark the topic, lines for a question, lines for an
# instruction). First match wins, so the more specific topics come first:
# "remind me about the meeting" is a reminder, not a calendar check, and "how
# much is my assignment worth" is about the weighting, not just Canvas. The
# line names the thing asked about where it can - "let me check the
# weighting", not "one sec" - and there are several of each, picked at random,
# so the same question doesn't get the same words every time.
_TOPICS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "reminders": (
        ("remind", "reminder", "reminders"),
        ("Let me look at your reminders.", "One sec, checking your reminders."),
        ("Sure, setting that up.", "Okay, I'll set that reminder.")),
    "alarms": (
        ("timer", "alarm", "alarms"),
        ("Let me check your alarms.",),
        ("Sure, setting that now.", "Okay, one sec.")),
    "weighting": (
        ("weigh", "weighs", "weight", "weighting", "weightings", "weighted", "worth"),
        ("Sure, let me check the weighting for you.",
         "Let me find out how much that's worth."),
        ("Sure, let me check the weighting for you.",)),
    "marks": (
        ("grade", "grades", "mark", "marks", "score", "scores", "results"),
        ("Let me check your marks.", "One sec, looking at your grades."),
        ("Let me check your marks.",)),
    "course material": (
        ("lecture", "lectures", "tutorial", "tutorials", "lab", "labs", "prelab",
         "slides", "readings"),
        ("Let me look at the course material.", "One sec, checking the course page."),
        ("Let me look at the course material.",)),
    "coursework": (
        ("assignment", "assignments", "assessment", "assessments", "coursework",
         "canvas", "quiz", "quizzes", "exam", "exams", "test", "tests", "midterm"),
        ("Let me check Canvas.", "One sec, checking your coursework."),
        ("Let me check Canvas.",)),
    "calendar": (
        ("calendar", "meeting", "meetings", "schedule", "appointment", "event",
         "events", "free", "busy"),
        ("Let me check your calendar.", "One sec, looking at your calendar.",
         "Checking your calendar now."),
        ("Sure, updating your calendar.", "Okay, one sec.")),
    "route": (
        ("navigate", "directions", "route", "drive", "traffic", "bus", "train"),
        ("Let me check the route.", "One sec, looking at the trip."),
        ("Sure, getting directions.",)),
    "notes": (
        ("notes", "jot", "draft"),
        ("Let me look through your notes.", "One sec, checking your notes."),
        ("Sure, one sec.",)),
    "notifications": (
        ("notification", "notifications", "messages", "texts"),
        ("Let me check your notifications.",),
        ("Sure, one sec.",)),
    "web": (
        ("weather", "search", "news", "google"),
        ("Let me look that up.", "One sec, I'll look that up."),
        ("Let me look that up.",)),
}

# Ways of asking about a topic that its words alone miss. "How did I go on the
# test" is asking for a mark without saying "mark".
_TOPIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "marks": re.compile(r"\bhow(?:d| did) i (?:go|do|went)\b|\bhow i (?:went|did)\b"),
}


def _mentions(word: str, marks: tuple[str, ...]) -> bool:
    """`word` is one of `marks`, or one with a letter too many - a plural, or
    a typo like "testt". Short marks must match exactly: "lab" isn't "labs"."""
    return word in marks or any(
        len(m) >= 4 and word.startswith(m) and len(word) - len(m) == 1 for m in marks)

# What's coming up for the user without naming where it's kept - "what do I
# have due next week", "anything on tomorrow?" - which takes the calendar and
# reminders together. Checked after _TOPICS, so "weather tomorrow" stays a
# lookup, and not when the question is about NOVA: "how was your day today"
# mentions a day but has nothing of the user's to check.
_AGENDA_WORDS = frozenset({
    "due", "deadline", "deadlines", "todo", "tasks", "assignment",
    "assignments", "upcoming", "plans", "planned", "agenda", "today",
    "tonight", "tomorrow", "week", "weekend", "month", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday",
})
# Before the model has read its prompt since a restart (llm.prompt_cached).
_WAKING = (
    "Give me a moment, I'm just waking up.",
    "One sec, I'm just waking up.",
)

_ABOUT_NOVA = frozenset({"you", "your", "youre", "yours"})
_AGENDA = (
    ("Let me have a look.", "One sec, I'll check.", "Let me see what's coming up."),
    ("Sure, one sec.", "Okay, on it."),
)


def phrase(text: str, user_id: Optional[Any] = None) -> Optional[str]:
    """The line to say while `text` is being answered, or None when nothing
    needs looking up or doing first - chat, a greeting, a plain question.
    While the model is waking up every turn is slow, so every turn hears so.

    Said the way the user has asked NOVA to talk (their standing instructions
    - "always talk like a pirate"), like the reply after it. Only the model
    can do that, so a user with standing instructions gets the model's line or
    nothing: a fixed line in plain English would break character."""
    if not text or not text.strip():
        return None
    waking = llm.prompt_cached is False
    standing = _standing_instructions(user_id) if user_id is not None else []
    if not MOCK_LLM:
        decided, line = _from_model(text, standing, waking)
        if decided:
            print(f"[ack] model {'line' if line else 'none'}")
            return line
    if standing:
        print("[ack] none - fixed lines can't follow standing instructions")
        return None
    if waking:
        print("[ack] waking")
        return random.choice(_WAKING)
    if not commands.is_request(text):
        return None
    line = keyword_phrase(text)
    print(f"[ack] keywords {'line' if line else 'none'}")
    return line


def _standing_instructions(user_id: Any) -> list[str]:
    """What the user has told NOVA about how to talk. Non-fatal: without them
    the line is plain, as it would have been."""
    try:
        return [f.text for f in persona.standing_instructions(user_id)]
    except Exception as e:
        print(f"[ack] standing instructions skipped: {type(e).__name__}")
        return []


def keyword_phrase(text: str) -> Optional[str]:
    """A line for the first topic `text` mentions, or None if it mentions
    none: "how are you" has nothing to check, so nothing is said first."""
    plain = text.lower().replace("'", "").replace("’", "")
    words = re.findall(r"[a-z]+", plain)
    asking = commands.is_question(text)
    for name, (marks, questions, instructions) in _TOPICS.items():
        pattern = _TOPIC_PATTERNS.get(name)
        if any(_mentions(w, marks) for w in words) or (pattern and pattern.search(plain)):
            return random.choice(questions if asking else instructions)
    if _AGENDA_WORDS.intersection(words) and not _ABOUT_NOVA.intersection(words):
        questions, instructions = _AGENDA
        return random.choice(questions if asking else instructions)
    return None


def _from_model(text: str, standing: list[str] = (), waking: bool = False) -> tuple[bool, Optional[str]]:
    """(whether the model decided, its line). Not decided - slow, failed, or
    a line too long to be a quick one - falls back to the keyword table; a
    decided None is the model saying nothing needs checking."""
    system = PROMPT
    if waking:
        system += WAKING_PROMPT
    if standing:
        system += STANDING_PROMPT + "".join(f"\n- {s}" for s in standing)
    try:
        line = llm.parse(
            system, text, _Ack, max_tokens=60, timeout=MODEL_TIMEOUT_S,
            max_retries=0, llm=llm.client(MODEL_TIMEOUT_S, 0, voice=False),
            # Part of answering someone, so it doesn't wait for their turn.
            wait_for_voice=False,
        ).ack
    except Exception as e:
        print(f"[ack] model skipped: {type(e).__name__}")
        return False, None
    line = (line or "").strip()
    if len(line) > MAX_CHARS:
        return False, None
    return True, line or None
