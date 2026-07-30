"""Save and load Function tools' gain values.

WHERE THE FILE LIVES, AND WHY IT MATTERS
Two files, with different jobs:

  the seed   `data/gain_store.seed.json`, committed, read-only. Where each dial
             starts on a fresh checkout - the team's decision about how proactive
             each Function should be before anyone has used it.
  the live   wherever NOVA_GAIN_STORE points, `data/gain_store.json` by default,
             gitignored. What reinforcement and the user's overrides have made of
             those dials since.

Splitting them fixes a real nuisance: the live file used to be tracked, so every
turn that moved a gain dirtied the working tree, `git status` was never clean
after a demo, and two people demoing produced a merge conflict in a file neither
had edited. It also makes the Controller testable - a test points the path at a
tmpdir and tunes gains without touching anyone's own.

Reads merge the two, live over seed. Writes only ever touch the live file.

Eventually this moves to Supabase alongside everything else; the seam is
GainStore, so that is a change here and nowhere else.
"""

import json
import os
from pathlib import Path
from typing import Optional

from .controller_gain import ControllerGain

# Committed. Every tool in tools/catalogue.py must appear here - there is a test
# for that, because a missing entry silently comes up at DEFAULT_GAIN instead.
DEFAULT_SEED_PATH = Path(__file__).parent / "data" / "gain_store.seed.json"

# Where the live file goes when NOVA_GAIN_STORE says nothing. Gitignored.
FALLBACK_LIVE_PATH = Path(__file__).parent / "data" / "gain_store.json"

# The environment variable that makes the path configuration rather than a
# constant - one deployment per user state, and one per test.
PATH_ENV_VAR = "NOVA_GAIN_STORE"


def resolve_path() -> Path:
    """Where learned gain is being kept for this process."""
    configured = os.environ.get(PATH_ENV_VAR, "").strip()
    return Path(configured) if configured else FALLBACK_LIVE_PATH


class GainStore:
    def __init__(
        self,
        path: Optional[Path] = None,
        seed_path: Path = DEFAULT_SEED_PATH,
    ) -> None:
        # Resolved at construction, not at import, so a test that sets the
        # environment variable before building a store is honoured.
        self.path = Path(path) if path is not None else resolve_path()
        self.seed_path = Path(seed_path)

    def load(self, name: str) -> Optional[ControllerGain]:
        """The saved gain for a tool, or None if neither file mentions it.

        None is meaningful: it is how ToolRegistry.register knows to fall back to
        a fresh DEFAULT_GAIN rather than a stale zero.
        """
        entry = self._merged().get(name)
        if entry is None:
            return None
        return ControllerGain(
            name=name,
            value=entry.get("value", 0.0),
            override=entry.get("override"),
        )

    def save(self, gain: ControllerGain) -> None:
        """Persist a tool's current gain to the live file."""
        data = self._read(self.path)
        data[gain.name] = {"value": gain.value, "override": gain.override}
        self._write(data)

    def load_all(self) -> dict[str, ControllerGain]:
        """Every gain either file knows about - one dial each for the Gain tab."""
        return {name: self.load(name) for name in self._merged()}

    # --- files ---------------------------------------------------------------

    def _merged(self) -> dict[str, dict]:
        """The seed with the live file laid over it.

        A tool nobody has tuned reads from the seed; once reinforcement or an
        override has moved it, the live value is the gain.
        """
        return {**self._read(self.seed_path), **self._read(self.path)}

    def _read(self, path: Path) -> dict[str, dict]:
        """One file's contents, or nothing.

        A half-written or hand-edited file reads as empty rather than raising:
        the dials are a preference and losing them costs a demo's tuning, while
        failing here would stop the backend starting at all.
        """
        if not path.exists():
            return {}
        try:
            with path.open("r") as f:
                loaded = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[gain] ignoring unreadable gain store {path}: {e}")
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _write(self, data: dict[str, dict]) -> None:
        """Write the live file, creating its directory if this is the first save."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w") as f:
            json.dump(data, f, indent=2)
