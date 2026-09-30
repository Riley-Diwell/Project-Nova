"""
notes_pipeline/ - what happens to a voice note after it is saved  (F5)

    chunking.py   - split long notes into ~200-word embedding windows
    summarise.py  - one structured-output Claude call per long note
    processor.py  - NotesPipelineProcessor, the NoteProcessor
                    the notes router calls after create/edit

Speech-to-text is not here: it runs on the phone (Vosk), and the server only
ever receives text. No audio reaches this service.
"""
from app.notes_pipeline.processor import NotesPipelineProcessor

__all__ = ["NotesPipelineProcessor"]
