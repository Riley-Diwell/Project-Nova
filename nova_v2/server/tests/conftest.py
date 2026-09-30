"""Suite-wide safety net.

persona.remember() calls a model to judge duplicates and contradictions, and
puts every fact under a topic, which a model then checks in the background -
and config.py loads server/.env. So without this, any test that writes a
belief could reach the model server. Every test gets table-driven stand-ins
instead, and the topic check runs only when a test asks (refresh()), never on a
background thread; tests that care install their own.
"""
import pytest

from persona_support import StubJudge

from app.store import persona
from app.store.persona import clusters


@pytest.fixture(autouse=True)
def _stub_persona_judge():
    judge = StubJudge()
    persona.set_judge(judge)
    yield judge
    persona.set_judge(None)


@pytest.fixture(autouse=True)
def _offline_topics():
    clusters.set_classifier(lambda texts: [clusters.guess_topic(None, t) for t in texts])
    clusters.set_background(None)
    yield
    clusters.set_classifier(None)
    clusters.set_background(clusters._executor.submit)
