"""Regression: a deleted memory-promoted fact is not re-created by the
statement pass, even with an extractor that returns a fact for every
utterance - and note content never feeds the statement pass at all."""
from notes_support import *  # noqa: F401,F403 - fixtures
from notes_support import USER

from app.store import consolidation, persona
from app.store.consolidation import StatedFact
from app.tools.functions.memory_tool import MemoryTool


def greedy_extractor(batch):
    """Returns a fact for every utterance it is shown - the worst case."""
    return [StatedFact(text=u["text"], category=["facts"], episode_id=u["id"]) for u in batch]


def voice_turn(stores, text, actions):
    fake = stores["memory"]
    episode_id = fake.append(USER, {"event_type": "voice", "event": {"type": "voice", "text": text}})
    fake.close(USER, episode_id, action={"actions": actions, "speech": "Noted."})
    return episode_id


def test_deleted_promoted_fact_is_not_recreated(stores):
    text = "remember I'm allergic to peanuts"
    tool_input = {"action": "save", "text": "Is allergic to peanuts", "category": ["facts", "health"]}
    MemoryTool().invoke(tool_input)
    voice_turn(stores, text, [{"tool": "memory", "input": tool_input, "trigger": "requested", "ran": True}])

    [fact] = persona.all_facts(USER)
    persona.delete(USER, fact.id)

    written = consolidation.consolidate_statements(USER, extractor=greedy_extractor)
    assert written == []
    assert persona.all_facts(USER) == []


def test_ordinary_voice_statement_is_still_extracted(stores):
    voice_turn(stores, "I study mechanical engineering", [])
    written = consolidation.consolidate_statements(USER, extractor=greedy_extractor)
    assert [f.text for f in written] == ["I study mechanical engineering"]


def test_legacy_note_rows_and_captures_never_feed_the_statement_pass(stores):
    fake = stores["memory"]
    fake.append(USER, {"event_type": "note", "event": {"text": "the lecturer said entropy always rises"}})
    fake.append(USER, {"event_type": "note_captured", "event": {"type": "note_captured", "kind": "capture"}})
    assert consolidation.consolidate_statements(USER, extractor=greedy_extractor) == []


def test_tombstone_keys_cover_episode_and_note():
    keys = persona.tombstone_keys({"episode_id": "e1", "note_id": "n1"})
    assert keys == ["episode:e1", "note:n1"]
    assert persona.tombstone_key({"note_id": "n1"}) == "note:n1"
