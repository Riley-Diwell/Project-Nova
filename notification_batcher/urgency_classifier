"""
urgency_classifier.py — Intent Surface → Notification Batcher Integration
Nova Project

Takes Georgia's Context JSON output (from gemini_intent_surface.py or
qwen_intent_surface.py) and classifies the urgency of an incoming
notification so the batcher doesn't need it manually specified.

The intent surface already knows:
  - time of day, location, calendar events (immediate context)
  - physiological signals — HR, posture (physiological context)
  - behavioural patterns (behavioural context)

This module uses all of that to answer: "how urgently should the user see this?"

HOW TO USE:
    from urgency_classifier import classify_urgency, notification_from_context
    from notification_batcher import NotificationBatcher, Notification

    # After Georgia's gather_context() returns a Context object:
    context = await gather_context(user_input, current_state)

    # Classify any incoming notification against that context:
    urgency = classify_urgency(
        notification_summary="Jay: are you coming to the meeting?",
        source="messages",
        context=context
    )
    # returns: "critical" | "high" | "low" | "ambient"

    # Or build a full Notification object ready to add to the batcher:
    notif = notification_from_context(
        id="msg_001",
        source="messages",
        summary="Jay: are you coming to the meeting?",
        context=context
    )
    batcher.add_notification(notif)

URGENCY RULES (in priority order):
  critical  → emergency keywords, battery critical, system failure
  high      → time-sensitive (meeting soon), direct message during work hours
  low       → general messages, reminders — held in lecture/focus mode
  ambient   → social media, newsletters — never surfaced proactively
"""

import json
from typing import Optional


# ── Keyword lists ─────────────────────────────────────────────────────────

CRITICAL_KEYWORDS = [
    "emergency", "urgent", "call me now", "accident", "hospital",
    "battery critical", "system failure", "sos"
]

HIGH_KEYWORDS = [
    "starting in", "starting soon", "on my way", "running late",
    "are you coming", "zoom link", "joining now", "assignment due",
    "deadline today", "can you join"
]

AMBIENT_SOURCES = [
    "instagram", "twitter", "tiktok", "facebook", "reddit",
    "newsletter", "promotions", "linkedin", "youtube", "snapchat"
]


# ── Context normaliser ────────────────────────────────────────────────────

def _parse_context(context) -> dict:
    """
    Normalise Georgia's Context to a flat dict.
    Works with her Pydantic object, a JSON string, or a plain dict.
    """
    if context is None:
        return {}

    if isinstance(context, str):
        try:
            raw = json.loads(context)
        except json.JSONDecodeError:
            return {}
        flat = {}
        for section in raw.values():
            if isinstance(section, dict):
                flat.update(section)
        return flat

    if isinstance(context, dict):
        flat = {}
        for v in context.values():
            if isinstance(v, dict):
                flat.update(v)
            else:
                flat.update(context)
                break
        return flat

    # Pydantic Context object (Georgia's class)
    flat = {}
    for section_name in ["immediate_context", "behavioural_context", "physiological_context"]:
        section = getattr(context, section_name, None)
        if section is None:
            continue
        if hasattr(section, "model_dump"):
            flat.update(section.model_dump())
        elif hasattr(section, "__dict__"):
            flat.update(vars(section))
    return flat


# ── Classifier ────────────────────────────────────────────────────────────

def classify_urgency(
    notification_summary: str,
    source: str,
    context=None,
) -> str:
    """
    Classify notification urgency using intent surface context.
    Returns: "critical" | "high" | "low" | "ambient"
    """

    summary_lower = notification_summary.lower()
    source_lower  = source.lower()
    ctx = _parse_context(context)

    # 1. Ambient sources always lose regardless of content
    if any(s in source_lower for s in AMBIENT_SOURCES):
        return "ambient"

    # 2. Critical keywords bypass everything
    if any(kw in summary_lower for kw in CRITICAL_KEYWORDS):
        return "critical"

    # 3. Physiological signal — if HR is elevated, downgrade non-critical noise
    #    Intent surface informs: user is already stressed, don't pile on
    try:
        hr = int(str(ctx.get("heart_rate", "N/A")))
        if hr > 100 and not any(kw in summary_lower for kw in HIGH_KEYWORDS):
            return "ambient"
    except (ValueError, TypeError):
        pass

    # 4. Calendar context from intent surface
    current_events = str(ctx.get("current_events", "N/A")).lower()
    future_events  = str(ctx.get("future_events",  "N/A")).lower()

    in_lecture   = any(kw in current_events for kw in
                       ["lecture", "tutorial", "class", "exam", "seminar", "workshop"])
    meeting_soon = any(kw in future_events for kw in
                       ["in 5", "in 10", "in 15", "starting soon", "now"])

    # 5. Location context
    location   = str(ctx.get("location", "N/A")).lower()
    at_uni     = any(kw in location for kw in ["anu", "university", "campus", "lecture hall"])

    # 6. Time context
    time_str = str(ctx.get("time", "N/A"))
    try:
        hour = int(time_str.split(":")[0]) if ":" in time_str else -1
    except (ValueError, AttributeError):
        hour = -1
    after_hours = (hour >= 22 or hour < 7)

    # 7. Classify using all signals together
    if any(kw in summary_lower for kw in HIGH_KEYWORDS):
        if after_hours:
            return "low"      # time-sensitive keywords but it's late — hold it
        if meeting_soon:
            return "high"     # explicit time signal
        if in_lecture:
            return "high"     # let lecture mode suppress it, but flag as high
        return "high"

    # In lecture + nothing urgent = hold as low
    if in_lecture:
        return "low"

    # After hours + no urgency = ambient
    if after_hours:
        return "ambient"

    return "low"


# ── Convenience builder ───────────────────────────────────────────────────

def notification_from_context(id, source, summary, context=None):
    """
    Build a Notification ready for the batcher, urgency auto-classified.
    Requires notification_batcher.py in the same folder.
    """
    from notification_batcher import Notification, Urgency
    urgency_str = classify_urgency(summary, source, context)
    return Notification(id=id, source=source, summary=summary, urgency=Urgency(urgency_str))


# ── Demo ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Urgency Classifier demo")
    print("Context: COMP3500 lecture, 9:05am, ANU, HR=68\n")

    mock_context = {
        "immediate_context": {
            "time": "09:05", "day": "Monday 14 July",
            "location": "ANU Llewellyn Hall",
            "current_events": "COMP3500 lecture 09:00-10:00",
            "future_events": "team meeting starting in 10 minutes",
        },
        "physiological_context": {
            "heart_rate": "68", "posture": "upright", "gaze": "attentive",
            "input_type": "system", "input_tone": "neutral"
        },
        "behavioural_context": {
            "similar_routines": "N/A", "conflicting_routines": "N/A",
            "previous_corrections": "N/A"
        }
    }

    tests = [
        ("messages",   "Jay: are you coming to the 9am?"),
        ("messages",   "Riley: can you review the PR?"),
        ("instagram",  "someone liked your photo"),
        ("system",     "Battery critical — 5% remaining"),
        ("calendar",   "Team meeting starting in 10 minutes"),
        ("messages",   "Mum: call me when you can"),
        ("newsletter", "Your weekly digest is ready"),
    ]

    for source, summary in tests:
        urgency = classify_urgency(summary, source, mock_context)
        print(f"  [{urgency:8s}]  {source:12s}  {summary}")

    print("\nThe lecture context is holding messages as low.")
    print("Meeting starting in 10 min overrides to high — time-sensitive.")
    print("Battery critical bypasses everything — always critical.")