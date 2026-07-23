"""

implementation (v1):
  Sends via Android intent over BLE → Nova app on phone triggers
  the actual message send 
  The Python backend composes the message and sends a structured
  command to the Android app; the app completes the send.

Called by nova_main.py after intent inference returns action="send_message".
"""

import sqlite3
import os
import json
import re
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
LOCAL_DB  = os.path.join(DATA_DIR, "local.db")


# Contact lookup 
# V1: hardcoded from Riley's seed data. V2: query local.db contacts properly.
CONTACTS = {
    "jay":    {"name": "Jay",   "number": "+61400000001"},
    "riley":  {"name": "Riley", "number": "+61400000002"},
    "naoise": {"name": "Naoise","number": "+61400000003"},
    "georgia":{"name": "Georgia","number":"+61400000004"},
    "josh":   {"name": "Josh",  "number": "+61400000005"},
    "alex":   {"name": "Alex",  "number": "alex@era.computer"},
    "mum":    {"name": "Mum",   "number": "+61400000006"},
    "jamie":  {"name": "Jamie Chen","number": "+61400000007"},
}


def send_message(natural_text: str, context=None) -> dict:
    """
    Parse a natural language send-message intent and dispatch it.

    natural_text: e.g. "message Jay that I'm running late"
                        "tell Riley the PR is approved"
                        "text Mum I'll be home by 7"
    context:      Georgia's Context object (for tone inference etc.)
    Returns:      dict with success bool and confirmation
    """

    recipient, message_body = _parse_message(natural_text)

    if not recipient:
        return {
            "success": False,
            "error":   "Couldn't identify a recipient — try 'message Jay that...'",
            "needs_clarification": True,
        }

    contact = CONTACTS.get(recipient.lower())
    if not contact:
        # Unknown contact — still try but flag it
        contact = {"name": recipient.title(), "number": "unknown"}

    # Log to local.db (Tier 1 — sent log, backed up) 
    _log_sent_message(contact, message_body)

    #Compose BLE command for Android app 
    # The Android app receives this and triggers an Android messaging intent
    ble_command = json.dumps({
        "cmd":       "send_message",
        "to_name":   contact["name"],
        "to_number": contact["number"],
        "body":      message_body,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    confirmation = f"Sending to {contact['name']}: \"{message_body}\""
    print(f"[TOOL:message] {confirmation}")
    print(f"[TOOL:message] BLE command → {ble_command}")

    return {
        "success":     True,
        "to":          contact["name"],
        "body":        message_body,
        "ble_command": ble_command,   # nova_main.py sends this to Android app
        "confirmation": confirmation,
    }


#Parsing 

def _parse_message(text: str):
    """
    Extract recipient and message body from natural text.
    Returns (recipient_str, body_str) or (None, text) if no recipient found.
    """
    text_stripped = text.strip()

    # Patterns: "message X that Y", "tell X Y", "text X Y", "send X a message Y"
    patterns = [
        r'(?:message|msg|text|tell|send)\s+(\w+)\s+(?:that\s+|a message\s+)?(.+)',
        r'(?:let|ask)\s+(\w+)\s+(?:know\s+)?(?:that\s+)?(.+)',
        r'(\w+)[,:]?\s+(.+)',   # fallback: first word might be a name
    ]

    for pat in patterns:
        m = re.match(pat, text_stripped, re.IGNORECASE)
        if m:
            candidate = m.group(1).lower()
            if candidate in CONTACTS or len(candidate) > 2:
                return m.group(1), m.group(2).strip()

    return None, text_stripped


def _log_sent_message(contact: dict, body: str):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(LOCAL_DB)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY, tier INTEGER NOT NULL,
                added_at TEXT NOT NULL, name_nonce BLOB NOT NULL,
                name_blob BLOB NOT NULL, content_nonce BLOB,
                content_blob BLOB, resident INTEGER NOT NULL)""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value BLOB)""")
        conn.commit()

        entry_name = f"Sent message to {contact['name']}"
        entry_data = json.dumps({"to": contact, "body": body,
                                 "sent_at": datetime.now(timezone.utc).isoformat()})
        try:
            from nova_storage import derive_key, encrypt, get_or_create_salt
            salt = get_or_create_salt(conn)
            key  = derive_key("Nova123", salt)
            nn, nb = encrypt(key, entry_name.encode())
            cn, cb = encrypt(key, entry_data.encode())
            conn.execute(
                "INSERT INTO items (tier,added_at,name_nonce,name_blob,content_nonce,content_blob,resident) "
                "VALUES (?,?,?,?,?,?,1)",
                (1, datetime.now(timezone.utc).isoformat(), nn, nb, cn, cb))
        except ImportError:
            conn.execute(
                "INSERT INTO items (tier,added_at,name_nonce,name_blob,content_nonce,content_blob,resident) "
                "VALUES (?,?,?,?,?,?,1)",
                (1, datetime.now(timezone.utc).isoformat(),
                 b"n", entry_name.encode(), b"n", entry_data.encode()))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[TOOL:message] log failed: {e}")


##### Demo 
if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    print("Tool: send_message — demo\n")
    tests = [
        "message Jay that I'm running late",
        "tell Riley the PR looks good",
        "text Mum I'll be home by 7",
        "send Georgia a message saying the ESP32s are here",
    ]
    for t in tests:
        print(f"  input: '{t}'")
        r = send_message(t)
        print(f"  → to: {r.get('to')}  body: \"{r.get('body')}\"")
        print(f"  → {r.get('confirmation')}\n")
