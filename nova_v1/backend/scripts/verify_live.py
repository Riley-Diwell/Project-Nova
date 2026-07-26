"""One live check for the whole backend against Supabase + local embeddings.

Runs three sections and cleans up after itself:
  1. Memory store   - append -> query -> patch outcome -> delete
  2. Persona store  - upsert -> semantic search -> correct in place
  3. Correction loop - a Memory episode drives a Persona search + update (5.5b)

Prereqs:
  * run backend/db/schema.sql in the Supabase SQL editor,
  * set SUPABASE_* in backend/.env.
Local embeddings download the bge model on first run, then cache.

Usage (from nova_v1/):
    python backend/scripts/verify_live.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable

from app import memory, persona
from app.config import settings
from app.reflection import run_reflection_pass

FOOD = ["opinions", "likes", "food"]


def check_memory() -> None:
    print("[memory] append -> query -> patch -> delete ...", end=" ")
    entry_id = memory.append(
        memory.MemoryEntry(
            event={"type": "user_request", "text": "verify_live"},
            action=memory.Action(tool="calendar.add_event", params={"title": "X"}),
        )
    )
    rows = memory.query(memory.MemoryFilter(tool="calendar.add_event", limit=5))
    assert any(r.id == entry_id for r in rows), "appended row not found"
    updated = memory.update_outcome(entry_id, memory.Outcome(status="accepted"))
    assert updated.outcome and updated.outcome.status == "accepted"
    memory.delete(entry_id)
    print("ok")


def check_persona() -> None:
    print("[persona] upsert -> semantic search ...", end=" ")
    fid = persona.upsert(persona.Fact(text="loves bagels for breakfast", category=FOOD))
    try:
        hits = persona.search(persona.PersonaQuery(text="what food does the user enjoy", limit=3))
        assert hits and "bagel" in hits[0].fact.text.lower(), f"bad top match: {hits}"
        print(f"ok -> top='{hits[0].fact.text}' sim={hits[0].similarity:.3f}")
    finally:
        persona.delete(fid)


def check_correction_loop() -> None:
    print("[loop] episode 'likes bagels' corrects belief 'dislikes bagels' ...", end=" ")
    fid = persona.upsert(persona.Fact(text="dislikes bagels", category=FOOD))
    entry_id = memory.append(
        memory.MemoryEntry(
            event={
                "type": "user_request",
                "text": "these bagels are actually great, I was wrong",
                "durable_claim": {"text": "likes bagels", "category": FOOD},
            },
            action=memory.Action(tool="notes.remember"),
        )
    )
    try:
        results = run_reflection_pass()
        mine = [r for r in results if r.entry_id == entry_id]
        assert mine and mine[0].action == "updated", f"expected update, got {results}"
        assert mine[0].fact_id == fid, "should have corrected the SAME belief in place"
        assert persona.get(fid).text == "likes bagels"
        print("ok -> corrected in place, no new row")
    finally:
        persona.delete(fid)
        memory.delete(entry_id)


def main() -> int:
    if not settings().supabase_configured:
        print("SKIP: SUPABASE_* not set. See backend/.env.example.")
        return 1
    check_memory()
    check_persona()
    check_correction_loop()
    print("\nPASS - memory, persona, and the correction loop are live.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
