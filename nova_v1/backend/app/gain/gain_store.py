"""Save and load Function tools' gain values.

WHERE GAIN LIVES, AND WHY IT MATTERS
Two sources, with different jobs:

  the seed   `data/gain_store.seed.json`, committed, read-only. Where each dial
             starts on a fresh checkout - the team's decision about how proactive
             each Function should be before anyone has used it.
  the live   the `public.tool_gain` Supabase table (db/schema.sql). What
             reinforcement and the user's overrides have made of those dials
             since.

Reads merge the two, live over seed. Writes only ever touch the live table.

This used to be a gitignored local JSON file, which worked for one dev
machine but not a deployed backend: Cloud Run containers are ephemeral and
can run more than one instance, so a local file silently reset on every
redeploy and could fork between instances. The seam was always meant to
move here - see db/schema.sql's tool_gain table, added ahead of this change -
so this is a change to this file and nowhere else.
"""

import json
from pathlib import Path
from typing import Optional

from .controller_gain import ControllerGain

try:
    from ..db import get_client            # app.gain.gain_store
except ImportError:                         # pragma: no cover
    from db import get_client               # gain.gain_store (cwd = backend/app)

# Committed. Every tool in tools/catalogue.py must appear here - there is a test
# for that, because a missing entry silently comes up at DEFAULT_GAIN instead.
DEFAULT_SEED_PATH = Path(__file__).parent / "data" / "gain_store.seed.json"

TABLE_NAME = "tool_gain"


class GainStore:
    def __init__(self, seed_path: Path = DEFAULT_SEED_PATH) -> None:
        self.seed_path = Path(seed_path)

    def load(self, name: str) -> Optional[ControllerGain]:
        """The saved gain for a tool, or None if neither source mentions it.

        None is meaningful: it is how ToolRegistry.register knows to fall back to
        a fresh DEFAULT_GAIN rather than a stale zero.
        """
        entry = self._merged().get(name)
        return self._to_gain(name, entry) if entry is not None else None

    def save(self, gain: ControllerGain) -> None:
        """Persist a tool's current gain to the live table."""
        get_client().table(TABLE_NAME).upsert({
            "tool_name": gain.name,
            "value": gain.value,
            "override": gain.override,
        }).execute()

    def load_all(self) -> dict[str, ControllerGain]:
        """Every gain either source knows about - one dial each for the Gain tab.

        Fetches the live table once rather than once per tool - this backs
        GET /tools/gain, called every time the Gain tab opens.
        """
        merged = self._merged()
        return {name: self._to_gain(name, entry) for name, entry in merged.items()}

    # --- sources ---------------------------------------------------------------

    def _to_gain(self, name: str, entry: dict) -> ControllerGain:
        return ControllerGain(
            name=name,
            value=entry.get("value", 0.0),
            override=entry.get("override"),
        )

    def _merged(self) -> dict[str, dict]:
        """The seed with the live table laid over it.

        A tool nobody has tuned reads from the seed; once reinforcement or an
        override has moved it, the live value is the gain.
        """
        return {**self._read_seed(), **self._read_live()}

    def _read_seed(self) -> dict[str, dict]:
        """The seed file's contents, or nothing.

        A missing or hand-edited-broken seed reads as empty rather than raising:
        the dials are a preference and losing them costs a demo's tuning, while
        failing here would stop the backend starting at all.
        """
        if not self.seed_path.exists():
            return {}
        try:
            with self.seed_path.open("r") as f:
                loaded = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[gain] ignoring unreadable seed {self.seed_path}: {e}")
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _read_live(self) -> dict[str, dict]:
        """Every row of the live table, or nothing if Supabase is unreachable.

        Same reasoning as _read_seed: a tool whose learned gain can't be
        fetched falls back to its seed default rather than stopping the
        backend from starting.
        """
        try:
            rows = (
                get_client()
                .table(TABLE_NAME)
                .select("tool_name,value,override")
                .execute()
                .data
            )
        except Exception as e:
            print(f"[gain] ignoring unreachable {TABLE_NAME} table: {e}")
            return {}
        return {row["tool_name"]: {"value": row["value"], "override": row["override"]} for row in rows}
