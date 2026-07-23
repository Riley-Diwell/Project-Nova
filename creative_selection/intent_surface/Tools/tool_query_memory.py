"""


Queries:
  1. vector database (semantic search over past notes/corrections)
  2. local.db (calendar events, contacts, reminders)
  Returns a answer ready to be spoken back.

Called by nova_main.py after intent inference returns action="query_memory"
or action="get_information".
"""

import sqlite3
import os
import json
from datetime import datetime, timezone
from typing import Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
LOCAL_DB  = os.path.join(DATA_DIR, "local.db")

# Naoise's vector DB endpoint (from his vector_database code)
VECTOR_DB_URL = "http://localhost:5000/query"   # adjust to his actual endpoint


def query_memory(natural_query: str, context=None, top_k: int = 3) -> dict:
    """
    Search Nova's memory for the most relevant items.

    natural_query: e.g. "what do I need to do today",
                        "what was that thing about the assignment",
                        "who did I say I'd call"
    context:       Georgia's Context object (used to weight recency, location)
    top_k:         number of results to return
    Returns:       dict with results list and synthesised spoken answer
    """

    results = []

    #1. Query Naoise's vector database 
    vector_results = _query_vector_db(natural_query, top_k)
    results.extend(vector_results)

    # 2. Query Riley's local.db for reminders + calendar items
    local_results = _query_local_db(natural_query)
    results.extend(local_results)

    # 3. Deduplicate and rank 
    results = _rank_results(results, natural_query, context)[:top_k]

    #4. Synthesise a spoken answer 
    if not results:
        spoken = "I don't have anything saved that matches that."
    elif len(results) == 1:
        spoken = results[0]["text"]
    else:
        items = ". ".join(r["text"] for r in results[:3])
        spoken = f"I found a few things: {items}"

    print(f"[TOOL:memory] query='{natural_query}' → {len(results)} results")
    for r in results:
        print(f"  [{r.get('score',0):.2f}] {r['text'][:80]}")

    return {
        "success": True,
        "query":   natural_query,
        "results": results,
        "spoken":  spoken,      # nova_main.py sends this to TTS → BLE → ear
    }


# Vector DB query 

def _query_vector_db(query: str, top_k: int) -> list:
    """
    Query Naoise's vector database. Falls back to empty list if unavailable.
    Adjust VECTOR_DB_URL to match his actual server endpoint.
    """
    try:
        import requests
        r = requests.post(
            VECTOR_DB_URL,
            json={"query": query, "top_k": top_k},
            timeout=3,
        )
        if r.status_code == 200:
            hits = r.json().get("results", [])
            return [{"text": h["text"], "score": h["score"], "source": "memory"}
                    for h in hits if h.get("score", 0) > 0.5]
    except Exception as e:
        print(f"[TOOL:memory] vector DB unavailable: {e}")

    # Demo fallback — sample memory entries 
    # Remove this in production once Naoise's DB is running
    demo_memories = [
        "Assignment due Friday for COMP3300, still need to write the discussion section.",
        "Meeting with Alex went well, he liked the creative selection approach.",
        "Feeling anxious about how much time I spend checking notifications.",
        "Good study session in the library today, phone was in bag the whole time.",
        "Team standup: Josh exploring XR, Georgia on ESP32 boards.",
    ]
    query_lower = query.lower()
    scored = []
    for m in demo_memories:
        words    = set(query_lower.split())
        m_words  = set(m.lower().split())
        overlap  = len(words & m_words) / max(len(words), 1)
        if overlap > 0:
            scored.append({"text": m, "score": overlap, "source": "memory_demo"})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


#Local DB query 

def _query_local_db(query: str) -> list:
    """Search Riley's local.db for reminders and calendar items."""
    if not os.path.exists(LOCAL_DB):
        return []
    results = []
    try:
        conn = sqlite3.connect(LOCAL_DB)
        rows = conn.execute(
            "SELECT name_blob, content_blob FROM items WHERE tier IN (0,1) AND resident=1"
        ).fetchall()
        conn.close()

        query_lower = query.lower()
        for name_blob, content_blob in rows:
            try:
                # Try decrypted read first
                from nova_storage import derive_key, decrypt, get_or_create_salt
                conn2 = sqlite3.connect(LOCAL_DB)
                salt  = get_or_create_salt(conn2)
                key   = derive_key("Nova123", salt)
                conn2.close()
                name = decrypt(key, b"nonce", name_blob).decode(errors="ignore")
            except Exception:
                try:
                    name = name_blob.decode(errors="ignore")
                except Exception:
                    continue

            words   = set(query_lower.split())
            n_words = set(name.lower().split())
            overlap = len(words & n_words) / max(len(words), 1)
            if overlap > 0.1:
                results.append({"text": name, "score": overlap * 0.8, "source": "local_db"})
    except Exception as e:
        print(f"[TOOL:memory] local DB query failed: {e}")
    return results


def _rank_results(results: list, query: str, context) -> list:
    """Sort by score descending, deduplicate near-identical strings."""
    seen = set()
    deduped = []
    for r in sorted(results, key=lambda x: x.get("score", 0), reverse=True):
        key = r["text"][:40].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped


##### Demo 
if __name__ == "__main__":
    print("Tool: query_memory — demo\n")
    queries = [
        "what do I need to finish for uni",
        "who did I say I'd call",
        "what was the thing about notifications",
        "what's on the team's plate",
    ]
    for q in queries:
        print(f"  query: '{q}'")
        r = query_memory(q)
        print(f"  spoken: \"{r['spoken']}\"\n")
