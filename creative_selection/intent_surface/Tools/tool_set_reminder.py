"""
tool_set_reminder.py — Nova V1 Reference Tool 1
"Set a reminder"

Why this function:
  - Beats the phone: no unlock, no open app, no type, no confirm — one sentence
  - No prolonged visual engagement: eyes-free, hands-free
  - High frequency: people set reminders multiple times a day
  - Value prop: demonstrable side-by-side with phone (stopwatch test)

Writes to Riley's nova_storage.py SQLite local.db as a Tier 0 item
(always on device, never leaves).

Called by nova_main.py after intent inference returns action="set_reminder".

Usage:
    from tool_set_reminder import set_reminder
    result = set_reminder("call Jay at 3pm", context)
    # result: {"success": True, "stored": "Reminder: call Jay at 15:00 today"}
"""

import sqlite3
import os
import json
import re
from datetime import datetime, timezone
from typing import Optional

# Path to local.db — same DATA_DIR as nova_storage.py
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
LOCAL_DB  = os.path.join(DATA_DIR, "local.db")


def set_reminder(natural_text: str, context=None) -> dict:
    """
    Parse a natural language reminder and store it in local.db.

    natural_text: e.g. "call Jay at 3pm", "submit the report by Friday"
    context:      Georgia's Context object (used to resolve relative times)
    Returns:      dict with success bool and human-readable confirmation
    """

    # Parse time from text (simple heuristic, no heavy NLP needed for V1)
    parsed_time = _parse_time(natural_text, context)
    clean_text  = _strip_time_phrases(natural_text)

    #Build the reminder entry 
    entry_name    = f"Reminder: {clean_text}"
    entry_content = json.dumps({
        "text":       clean_text,
        "raw_input":  natural_text,
        "due":        parsed_time,
        "created":    datetime.now(timezone.utc).isoformat(),
        "done":       False,
    })

    # Write to DB (Tier 0 — always local)
    try:
        conn = sqlite3.connect(LOCAL_DB)

        # Ensure DB is initialised (may not exist yet in a fresh run)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                tier INTEGER NOT NULL,
                added_at TEXT NOT NULL,
                name_nonce BLOB NOT NULL,
                name_blob BLOB NOT NULL,
                content_nonce BLOB,
                content_blob BLOB,
                resident INTEGER NOT NULL
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value BLOB)""")
        conn.commit()

        # Try to use Riley's encryption if available
        try:
            from nova_storage import derive_key, encrypt, get_or_create_salt
            salt = get_or_create_salt(conn)
            key  = derive_key("Nova123", salt)  # same passcode as nova_storage.py
            name_nonce, name_blob       = encrypt(key, entry_name.encode())
            content_nonce, content_blob = encrypt(key, entry_content.encode())
            conn.execute(
                "INSERT INTO items (tier,added_at,name_nonce,name_blob,content_nonce,content_blob,resident) "
                "VALUES (?,?,?,?,?,?,1)",
                (0, datetime.now(timezone.utc).isoformat(),
                 name_nonce, name_blob, content_nonce, content_blob))
            conn.commit()
            encrypted = True
        except ImportError:
            # nova_storage not available — store plaintext for dev/testing
            conn.execute(
                "INSERT INTO items (tier,added_at,name_nonce,name_blob,content_nonce,content_blob,resident) "
                "VALUES (?,?,?,?,?,?,1)",
                (0, datetime.now(timezone.utc).isoformat(),
                 b"nonce", entry_name.encode(),
                 b"nonce", entry_content.encode()))
            conn.commit()
            encrypted = False

        conn.close()

        confirmation = f"Reminder set: {clean_text}"
        if parsed_time:
            confirmation += f" — due {parsed_time}"

        print(f"[TOOL:reminder] {confirmation} (encrypted={encrypted})")
        return {"success": True, "stored": confirmation, "due": parsed_time}

    except Exception as e:
        print(f"[TOOL:reminder] ERROR: {e}")
        return {"success": False, "error": str(e)}


#Helpers

def _parse_time(text: str, context=None) -> Optional[str]:
    """
    Extract a time/date from natural text.
    Returns ISO string or human-readable string, or None if no time found.
    V1: simple regex patterns. V2: use an NLP library or ask the LLM.
    """
    text_lower = text.lower()
    now = datetime.now()

    # "at 3pm", "at 14:30"
    m = re.search(r'at (\d{1,2})(?::(\d{2}))?\s*(am|pm)?', text_lower)
    if m:
        hour = int(m.group(1))
        mins = int(m.group(2)) if m.group(2) else 0
        ampm = m.group(3)
        if ampm == "pm" and hour < 12: hour += 12
        if ampm == "am" and hour == 12: hour = 0
        return f"{now.strftime('%Y-%m-%d')} {hour:02d}:{mins:02d}"

    # "in 30 minutes", "in 2 hours"
    m = re.search(r'in (\d+)\s*(minute|hour|min|hr)', text_lower)
    if m:
        val  = int(m.group(1))
        unit = m.group(2)
        from datetime import timedelta
        delta = timedelta(minutes=val) if "min" in unit else timedelta(hours=val)
        return (now + delta).strftime("%Y-%m-%d %H:%M")

    # "tomorrow"
    if "tomorrow" in text_lower:
        from datetime import timedelta
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")

    # "friday", "monday" etc
    days = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    for i, day in enumerate(days):
        if day in text_lower:
            delta = (i - now.weekday()) % 7 or 7
            from datetime import timedelta
            return (now + timedelta(days=delta)).strftime("%Y-%m-%d")

    return None


def _strip_time_phrases(text: str) -> str:
    """Remove time phrases so the stored label is clean."""
    patterns = [
        r'\bat \d{1,2}(?::\d{2})?\s*(?:am|pm)?',
        r'\bin \d+ (?:minutes?|hours?|mins?|hrs?)',
        r'\btomorrow\b', r'\btoday\b',
        r'\bon (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)',
        r'\bby (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)',
    ]
    result = text
    for p in patterns:
        result = re.sub(p, '', result, flags=re.IGNORECASE)
    return result.strip(" ,.")


#### Demo 
if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    print("Tool: set_reminder — demo\n")
    tests = [
        "call Jay at 3pm",
        "submit the assignment by Friday",
        "buy groceries in 30 minutes",
        "check the build tomorrow",
    ]
    for t in tests:
        result = set_reminder(t)
        print(f"  input:  '{t}'")
        print(f"  result: {result}\n")
