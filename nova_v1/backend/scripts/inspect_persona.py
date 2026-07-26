"""Show where Persona vectors live: the `embedding` column of the Supabase
`persona` table. Prints row count and a preview of each stored vector.

Usage (from nova_v1/):
    python backend/scripts/inspect_persona.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable

from app.config import settings
from app.db import get_client


def _as_list(emb) -> list[float]:
    """pgvector may come back as a JSON string or an already-parsed list."""
    if isinstance(emb, str):
        return json.loads(emb)
    return emb or []


def main() -> int:
    if not settings().supabase_configured:
        print("SKIP: SUPABASE_* not set. See backend/.env.example.")
        return 1

    db = get_client()
    try:
        res = db.table("persona").select("id, text, category, embedding").execute()
    except Exception as exc:  # table missing = schema not applied yet
        print(f"Could not read `persona`: {exc}")
        print("\n-> Run backend/db/schema.sql in the Supabase SQL editor first.")
        return 1

    rows = res.data
    print(f"Table: public.persona   Rows: {len(rows)}")
    print("Vectors are stored in the `embedding` column (pgvector, 1024-dim).\n")

    if not rows:
        print("(no facts stored yet - upsert some, or run verify_live.py)")
        return 0

    for r in rows:
        vec = _as_list(r["embedding"])
        preview = ", ".join(f"{x:+.4f}" for x in vec[:5])
        print(f"  id={r['id']}")
        print(f"    text     : {r['text']}")
        print(f"    category : {r.get('category')}")
        print(f"    embedding: dim={len(vec)}  [{preview}, ...]\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
