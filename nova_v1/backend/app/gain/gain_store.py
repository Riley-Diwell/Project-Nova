"""
Save and load Function tools' gain values. 

Each tool's gain is stored in a JSON file so it is kept
when the backend is restarted. Will eventually be migrated to supabase?
"""

import json
from pathlib import Path
from typing import Optional

from .controller_gain import ControllerGain

DEFAULT_PATH = Path(__file__).parent / "data" / "gain_store.json"


class GainStore:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        # path to JSON file
        self.path = Path(path)

    def load(self, name: str) -> Optional[ControllerGain]:
        """
        Load the saved gain for a tool. Returns None if the tool has not been
        saved before.
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
        """
        Save a tool's current gain.
        """
        data = self._read_all()
        data[gain.name] = {"value": gain.value, "override": gain.override}
        self._write_all(data)

    def load_all(self) -> dict[str, ControllerGain]:
        """
        Load every saved gain.
        """
        return {name: self.load(name) for name in self._read_all()}

    def _read_all(self) -> dict[str, dict]:
        """
        Read all saved gain data from the JSON file.
        """
        # if the file doesnt exist return an empty dictionary
        if not self.path.exists():
            return {}
        
        with self.path.open("r") as f:
            return json.load(f)

    def _write_all(self, data: dict[str, dict]) -> None:
        """
        Write all gain data to the JSON file.
        """
        # create the folder if it doesnt exist yet
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with self.path.open("w") as f:
            json.dump(data, f, indent=2)