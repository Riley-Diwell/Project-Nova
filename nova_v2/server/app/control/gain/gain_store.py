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

WHOSE DIALS
Every account has its own. The live
table is keyed (user_id, tool_name), and a GainStore is bound to one user with
for_user(); an unbound one reads the seed alone - which is what the shared
ToolRegistry starts from at import, before there is anyone to ask about.
Reads go to the table on every turn rather than once at start-up, so a dial
moved on one Cloud Run instance is seen by the others on the next turn.
"""

import json
from pathlib import Path
from typing import Optional, Union
from uuid import UUID

from app.control.gain.controller_gain import ControllerGain
from app.core.db import get_client

# Committed. Every tool in tools/core/catalogue.py must appear here - there is a
# test for that, because a missing entry silently comes up at DEFAULT_GAIN instead.
DEFAULT_SEED_PATH = Path(__file__).parent / "data" / "gain_store.seed.json"

TABLE_NAME = "tool_gain"

UserId = Union[UUID, str]


class GainStore:
    def __init__(self, seed_path: Path = DEFAULT_SEED_PATH, user_id: Optional[UserId] = None) -> None:
        self.seed_path = Path(seed_path)
        # None: seed only, and save() has nowhere to write. See the header.
        self.user_id = str(user_id) if user_id is not None else None

    def for_user(self, user_id: UserId) -> "GainStore":
        """The same seed, with this user's live dials laid over it."""
        return GainStore(self.seed_path, user_id)

    def load_all(self) -> dict[str, ControllerGain]:
        """Every tool either source mentions, as this user's gains."""
        return {name: self._to_gain(name, entry) for name, entry in self._merged().items()}

    def load(self, name: str) -> Optional[ControllerGain]:
        """The saved gain for a tool, or None if neither source mentions it.

        None is meaningful: it is how ToolRegistry.register knows to fall back to
        a fresh DEFAULT_GAIN rather than a stale zero.
        """
        entry = self._merged().get(name)
        return self._to_gain(name, entry) if entry is not None else None

    def save(self, gain: ControllerGain) -> None:
        """Persist a tool's current gain to the live table.

        Non-fatal like every other Supabase touch in this file: an unconfigured
        or unreachable table must not crash the request that got the caller here
        (PUT /tools/gain/{name}, or reinforcement after an outcome). The change
        already landed on the in-memory ControllerGain before this was called,
        so the override still takes effect for this process - it just will not
        survive a restart. Found by actually running the server: this was the
        one Supabase touch point in the gain layer without the same guard as
        _read_seed/_read_live, so an unconfigured backend turned a PUT into a
        500 instead of degrading like every read here does.
        """
        if self.user_id is None:
            # Nobody's dial - the shared registry's seed defaults. Writing it
            # would need an owner the table requires and this store hasn't got.
            print(f"[gain] save skipped for {gain.name!r}: no user")
            return
        try:
            get_client().table(TABLE_NAME).upsert({
                "user_id": self.user_id,
                "tool_name": gain.name,
                "value": gain.value,
                "override": gain.override,
            }, on_conflict="user_id,tool_name").execute()
        except Exception as e:
            print(f"[gain] save skipped for {gain.name!r}: {e}")

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
        """This user's rows of the live table, or nothing if Supabase is
        unreachable or the store isn't bound to a user.

        Same reasoning as _read_seed: a tool whose learned gain can't be
        fetched falls back to its seed default rather than stopping the
        backend from starting.
        """
        if self.user_id is None:
            return {}
        try:
            rows = (
                get_client()
                .table(TABLE_NAME)
                .select("tool_name,value,override")
                .eq("user_id", self.user_id)
                .execute()
                .data
            )
        except Exception as e:
            print(f"[gain] ignoring unreachable {TABLE_NAME} table: {e}")
            return {}
        return {row["tool_name"]: {"value": row["value"], "override": row["override"]} for row in rows}
