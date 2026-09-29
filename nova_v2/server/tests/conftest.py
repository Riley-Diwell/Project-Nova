"""Suite-wide safety net.

persona.remember() calls a model to judge duplicates and contradictions, and
config.py loads server/.env - so without this, any test that writes a belief
next to a similar one could reach Claude with a real key. Every test gets a
table-driven stub instead; tests that care about verdicts install their own.
"""
import pytest

from persona_support import StubJudge

from app.store import persona


@pytest.fixture(autouse=True)
def _stub_persona_judge():
    judge = StubJudge()
    persona.set_judge(judge)
    yield judge
    persona.set_judge(None)
