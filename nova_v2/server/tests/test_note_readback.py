"""A spoken note keeps what was heard and gains what Nova thinks was meant; a
note Nova writes comes out titled and laid out."""
from types import SimpleNamespace

import pytest

from notes_support import *  # noqa: F401,F403 - fixtures
from notes_support import USER, make_note

from app.api.notes import _row, plain_text
from app.notes_pipeline import interpret, processor as processor_mod
from app.notes_pipeline.interpret import Interpreter, should_interpret
from app.notes_pipeline.processor import NotesPipelineProcessor
from app.notes_pipeline.summarise import MockSummariser
from app.store import notes
from app.store.notes import NotePatch, NoteStt
from app.store.notes.export import note_markdown
from app.tools.functions.memory_tool import MemoryTool

HEARD = "in the light of the mood a little egg lay on a leaf"
MEANT = "In the light of the moon,\na little egg lay on a leaf."


class FakeLLM:
    """The OpenAI-compatible client llm.parse calls, answering `reply`."""

    def __init__(self, reply):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._reply = reply

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=interpret.Interpretation(text=self._reply).model_dump_json()),
            finish_reason="stop")])


class StubInterpreter:
    def __init__(self, meant):
        self.meant, self.seen = meant, []

    def interpret(self, note):
        self.seen.append(note.text)
        return self.meant


# --- which notes are read back -------------------------------------------------------

@pytest.mark.parametrize("fields, expected", [
    ({"source": "device_voice"}, True),
    ({"source": "phone_voice", "kind": "dictation"}, True),
    ({"source": "assistant", "stt": NoteStt()}, True),        # "note ..." said to Nova
    ({"source": "assistant"}, False),                         # a note Nova wrote
    ({"source": "typed"}, False),
    ({"source": "device_voice", "kind": "capture"}, False),   # a capture gets a summary
])
def test_only_heard_notes_are_read_back(stores, fields, expected):
    note = notes.create(USER, make_note(HEARD, **fields))
    assert should_interpret(note) is expected


def test_a_long_dictation_is_not_read_back(stores):
    note = notes.create(USER, make_note("word " * (interpret.INTERPRET_MAX_WORDS + 1), source="device_voice"))
    assert not should_interpret(note)


# --- the interpreter ----------------------------------------------------------------

def test_the_interpreter_sends_the_heard_text_as_content(stores):
    client = FakeLLM(MEANT)
    note = notes.create(USER, make_note(HEARD, source="device_voice"))
    assert Interpreter(client=client).interpret(note) == MEANT
    [call] = client.calls
    assert f"<heard>\n{HEARD}\n</heard>" in call["messages"][1]["content"]


def test_an_unchanged_reading_is_not_kept(stores):
    note = notes.create(USER, make_note(HEARD, source="device_voice"))
    assert Interpreter(client=FakeLLM(HEARD + "  ")).interpret(note) is None


# --- the processor ---------------------------------------------------------------------

def test_a_voice_note_is_read_back_in_the_background_and_kept_beside_the_heard_text(stores, monkeypatch):
    queued = []
    monkeypatch.setattr(processor_mod, "_background", queued.append)
    proc = NotesPipelineProcessor(summariser=MockSummariser(), interpreter=StubInterpreter(MEANT))
    note = notes.create(USER, make_note(HEARD, source="device_voice"))

    saved = proc.after_create(note)
    assert saved.interpreted_text is None and len(queued) == 1   # not waited on
    queued[0]()

    stored = notes.get(USER, note.id)
    assert stored.text == HEARD                                  # the record is untouched
    assert stored.interpreted_text == MEANT


def test_a_typed_note_is_not_queued(stores, monkeypatch):
    queued = []
    monkeypatch.setattr(processor_mod, "_background", queued.append)
    proc = NotesPipelineProcessor(summariser=MockSummariser(), interpreter=StubInterpreter(MEANT))
    proc.after_create(notes.create(USER, make_note(HEARD, source="typed")))
    assert queued == []


def test_an_edit_made_while_reading_wins(stores):
    note = notes.create(USER, make_note(HEARD, source="device_voice"))
    notes.update(USER, note.id, NotePatch(text="my own fix"))
    NotesPipelineProcessor(summariser=MockSummariser(), interpreter=StubInterpreter(MEANT)).interpret(note)
    assert notes.get(USER, note.id).interpreted_text is None


def test_editing_the_text_drops_the_reading(stores):
    note = notes.create(USER, make_note(HEARD, source="device_voice"))
    notes.set_interpretation(USER, note.id, MEANT)
    notes.update(USER, note.id, NotePatch(text=MEANT))            # "Use this version"
    stored = notes.get(USER, note.id)
    assert stored.text == MEANT and stored.interpreted_text is None


def test_a_failed_read_back_leaves_the_note_alone(stores):
    class Down:
        def interpret(self, note):
            raise RuntimeError("model unreachable")

    note = notes.create(USER, make_note(HEARD, source="device_voice"))
    NotesPipelineProcessor(summariser=MockSummariser(), interpreter=Down()).interpret(note)
    assert notes.get(USER, note.id).text == HEARD


def test_export_shows_both(stores):
    note = notes.create(USER, make_note(HEARD, source="device_voice"))
    notes.set_interpretation(USER, note.id, MEANT)
    md = note_markdown(notes.get(USER, note.id))
    assert md.index(HEARD) < md.index("What Nova thinks you said") < md.index(MEANT)


# --- through the memory tool -----------------------------------------------------------

@pytest.fixture
def queued(stores, monkeypatch):
    jobs = []
    monkeypatch.setattr(processor_mod, "_background", jobs.append)
    notes.set_processor(NotesPipelineProcessor(summariser=MockSummariser(), interpreter=StubInterpreter(MEANT)))
    return jobs


def test_note_said_to_nova_is_marked_heard_and_read_back(queued):
    result = MemoryTool().invoke({"action": "save", "text": HEARD, "heard": True})
    note = notes.get(USER, result["note_id"])
    assert note.stt is not None and len(queued) == 1
    queued[0]()
    assert notes.get(USER, note.id).interpreted_text == MEANT


def test_a_note_nova_writes_keeps_its_title_and_layout_and_is_not_read_back(queued):
    text = "## Key ideas\n- Zero-padding\n- Windowing\n\n## Questions\n- Q1: max doppler 500 Hz"
    result = MemoryTool().invoke({"action": "save", "title": "DSP week 10 prep", "text": text,
                                  "category": ["facts", "courses"]})
    note = notes.get(USER, result["note_id"])                    # a note, despite the category
    assert note.title == "DSP week 10 prep" and note.text == text
    assert note.stt is None and queued == []


# --- list rows ------------------------------------------------------------------------

def test_list_preview_drops_the_markup(stores):
    text = "## Key ideas\n- **Zero-padding** sharpens the FFT\n1. Windowing\n\nQ1: 500 Hz"
    assert plain_text(text) == "Key ideas Zero-padding sharpens the FFT Windowing Q1: 500 Hz"
    note = notes.create(USER, make_note(text, title="DSP prep"))
    assert _row(note).preview.startswith("Key ideas Zero-padding")
