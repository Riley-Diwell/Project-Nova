"""Every registered tool has a seed gain - gain_store.py says there is a test
for this, because a missing entry silently comes up at DEFAULT_GAIN."""
import json

from app.control.gain.gain_store import DEFAULT_SEED_PATH
from app.tools.core.catalogue import CLIENT_TOOLS, DEVICE_TOOLS, build_registry


def registered_names():
    # No gain store: this is about the catalogue, not Supabase.
    return build_registry(gain_store=None).all_names()


def test_every_registered_tool_has_a_seed_entry():
    seed = json.loads(DEFAULT_SEED_PATH.read_text())
    missing = [name for name in registered_names() if name not in seed]
    assert missing == []


def test_client_and_device_tools_are_registered():
    names = set(registered_names())
    assert CLIENT_TOOLS <= names
    assert DEVICE_TOOLS <= names


def test_reminder_seeds():
    seed = json.loads(DEFAULT_SEED_PATH.read_text())
    assert seed["set_reminder"]["value"] == 0.15
    assert seed["update_reminder"]["value"] == 0.2
