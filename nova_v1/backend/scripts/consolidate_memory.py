"""
scripts/consolidate_memory.py - turn repeated episodes into durable Persona facts.

WHAT THIS FILE IS
The CLI front end to app/consolidation. The episodic log records what happened;
this reads it back, counts what keeps happening, and writes the result into
Persona as facts that a vector search can find long after the episodes
themselves have scrolled out of the Intent Surface's last-five window.

There are two passes, and they answer different questions:

  TRENDS     - what does this user keep doing? Counted from repeated tool
               calls; needs MIN_SUPPORT episodes before it means anything.
  STATEMENTS - what has this user told me about themselves? Read from what
               they actually said; one utterance is enough, because "I like
               bagels" said once is a fact, not a hypothesis.

    python scripts/consolidate_memory.py candidates       # trends, counting only, no Claude
    python scripts/consolidate_memory.py preview          # trends, phrased, no writes
    python scripts/consolidate_memory.py statements       # statements, no writes
    python scripts/consolidate_memory.py run              # BOTH passes, writes
    python scripts/consolidate_memory.py trends-only      # one pass, writes
    python scripts/consolidate_memory.py statements-only  # the other, writes
    python scripts/consolidate_memory.py status           # what Persona already holds

`candidates` is the one to reach for when something looks wrong: it stops after
the counting stage, so it costs nothing and shows you exactly what the model
would have been asked to phrase.

Needs SUPABASE_URL / SUPABASE_SERVICE_KEY in backend/.env, plus
ANTHROPIC_API_KEY for `run` and `preview` (not for `candidates`). The first run
also downloads the embedding model - see the README.

Run from backend/:
    .venv\\Scripts\\python.exe scripts\\consolidate_memory.py preview
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.consolidation import (  # noqa: E402
    MIN_SUPPORT,
    consolidate,
    consolidate_statements,
    consolidate_trends,
    find_candidates,
    preview,
    preview_statements,
)
from app.consolidation.models import SOURCE_DERIVED  # noqa: E402


def _episodes():
    from app import memory
    return memory.all()


def cmd_candidates(args: argparse.Namespace) -> int:
    """Counting only - no Claude call, no writes."""
    candidates = find_candidates(_episodes(), min_support=args.min_support)
    if not candidates:
        print(f"No repetition reaches support >= {args.min_support}.")
        return 0

    print(f"{len(candidates)} candidate(s) at support >= {args.min_support}:\n")
    for c in candidates:
        hours = sorted(set(c.hours))
        print(f"  {c.signal}")
        print(f"    value    : {c.value}")
        print(f"    support  : {c.support} episode(s) over {c.span_days} day(s)")
        print(f"    hours    : {hours or 'unknown'}")
        if c.exemplars:
            print(f"    said     : {c.exemplars[0]!r}")
            for extra in c.exemplars[1:]:
                print(f"               {extra!r}")
        print()
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    facts = preview(min_support=args.min_support)
    if not facts:
        print("Nothing to write.")
        return 0
    print(f"\nWould write {len(facts)} fact(s):\n")
    for f in facts:
        print(f"  {f.text}")
        print(f"    category   : {f.category}")
        print(f"    confidence : {f.confidence}  (support={f.candidate.support})")
        print()
    print("Nothing was written. Re-run with `run` to store these.")
    return 0


def cmd_statements(args: argparse.Namespace) -> int:
    """The statement pass on its own, without writing."""
    facts = preview_statements()
    if not facts:
        print("No new stated facts. (Every utterance has already been read.)")
        return 0
    print(f"\nWould write {len(facts)} stated fact(s):\n")
    for f in facts:
        print(f"  {f.text}")
        print(f"    category : {f.category}")
        print(f"    said     : {f.quote!r}")
        print()
    print("Nothing was written. Re-run with `run` to store these.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Both passes: what the user does repeatedly, and what they said outright."""
    written = consolidate(min_support=args.min_support)
    derived, stated = written["derived"], written["stated"]

    if not derived and not stated:
        print("Nothing to write.")
        return 0

    if derived:
        print(f"\nDerived from repeated behaviour ({len(derived)}):\n")
        for f in derived:
            print(f"  {f.text}  ({f.category}, confidence {f.confidence}, "
                  f"support {f.candidate.support})")
    if stated:
        print(f"\nStated by the user ({len(stated)}):\n")
        for f in stated:
            print(f"  {f.text}  ({f.category})")
            print(f"      from: {f.quote!r}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """What Persona holds now, derived facts separated from stated ones."""
    from app.db import get_client

    rows = get_client().table("persona").select(
        "id,text,category,confidence,metadata,updated_at"
    ).order("updated_at", desc=True).execute().data

    derived = [r for r in rows if (r.get("metadata") or {}).get("source") == SOURCE_DERIVED]
    stated = [r for r in rows if r not in derived]

    print(f"Persona holds {len(rows)} fact(s): "
          f"{len(derived)} derived, {len(stated)} stated.\n")

    if derived:
        print("Derived from episodic memory:")
        for r in derived:
            meta = r.get("metadata") or {}
            print(f"  {r['text']}")
            print(f"    {r['category']}  confidence={r['confidence']}  "
                  f"support={meta.get('support')}  signal={meta.get('signal')}")
        print()

    if stated:
        print("Stated by the user:")
        for r in stated:
            print(f"  {r['text']}  {r['category']}")
    return 0


COMMANDS = {
    "candidates": cmd_candidates,
    "preview": cmd_preview,
    "statements": cmd_statements,
    "run": cmd_run,
    "trends-only": lambda a: cmd_run_one(consolidate_trends(min_support=a.min_support)),
    "statements-only": lambda a: cmd_run_one(consolidate_statements()),
    "status": cmd_status,
}


def cmd_run_one(facts: list) -> int:
    """Report one pass's writes, for the -only commands."""
    if not facts:
        print("Nothing to write.")
        return 0
    print(f"\nWrote {len(facts)} fact(s):\n")
    for f in facts:
        print(f"  {f.text}  ({f.category})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consolidate episodic memory into durable Persona facts.",
    )
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument(
        "--min-support", type=int, default=MIN_SUPPORT,
        help=f"episodes needed before a repetition counts (default {MIN_SUPPORT})",
    )
    args = parser.parse_args()
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
