"""
Persists each tool's ControllerGain (learned value + optional override)
to disk as a small JSON file, keyed by tool name, so gain survives a
backend restart. This is the load/save half of the two TODOs left in
registry.py and reinforcement.py:

    stored = gain_store.load(tool.name)
    registry.register(tool, gain=stored)           # registry.py, on startup

    reinforcer.reinforce(name, outcome)
    gain_store.save(registry.get_gain(name))       # after a reinforcement step

Neither of those files calls into this one yet — this just implements
the store itself, so wiring it in later is a one-line change at each
call site rather than a redesign.

Backend is a flat JSON file rather than Supabase for now: the design doc
scopes Supabase specifically to Persona (5.4) and Memory (5.5) — it
never says gain has to live there too — and nothing in this repo has
Supabase credentials or a client library configured yet (see
environment.yml). A JSON file meets the actual bar, "gain survives a
restart," with zero new dependencies. If gain ever needs to live in
Supabase too — e.g. so it persists across devices rather than just this
one backend process — swap this file's internals; load()/save() are the
seam, nothing in tools/ or the rest of gain/ needs to change.

Not thread-safe — reads the whole file, mutates one entry, writes the
whole file back. Fine for V1's single-process demo; would need a lock
(or a real database) under concurrent writers.
"""

import json
from pathlib import Path
from typing import Optional

from .controller_gain import ControllerGain

DEFAULT_PATH = Path(__file__).parent / "data" / "gain_store.json"


class GainStore:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = Path(path)

    def load(self, name: str) -> Optional[ControllerGain]:
        """
        Reconstruct `name`'s persisted ControllerGain, or None if it's
        never been saved (e.g. a brand-new tool on first startup).
        Callers should fall back to a fresh default-gain instance in
        that case — registry.register() already does this when no
        gain is passed in.
        """
        entry = self._read_all().get(name)
        if entry is None:
            return None
        return ControllerGain(
            name=name,
            value=entry["value"],
            override=entry.get("override"),
        )

    def save(self, gain: ControllerGain) -> None:
        """Persist `gain`'s current value and override, keyed by its name."""
        data = self._read_all()
        data[gain.name] = {"value": gain.value, "override": gain.override}
        self._write_all(data)

    def load_all(self) -> dict[str, ControllerGain]:
        """
        Every persisted gain, keyed by tool name — for bulk-loading on
        startup instead of one load() call per known tool name.
        """
        return {name: self.load(name) for name in self._read_all()}

    def _read_all(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        with self.path.open("r") as f:
            return json.load(f)

    def _write_all(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w") as f:
            json.dump(data, f, indent=2)