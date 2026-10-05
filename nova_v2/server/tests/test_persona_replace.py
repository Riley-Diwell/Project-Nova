"""A new statement that overrules an older belief is a belief of its own: it
gets its own id, so the Knowledge Map's History dates it from when it was said,
not from when the belief it replaced was first learned. A Knowledge Map edit is
the exception - it rewrites the edited belief in place."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from notes_support import USER
from persona_support import GroupEmbedder

from app.store import persona
from app.store.persona import Fact, InMemoryPersonaStore, RememberAction


@pytest.fixture
def store(_stub_persona_judge):
    embedder = GroupEmbedder({"Is an engineering student": "study", "Is a physics student": "study",
                              "Is a music student": "study"})
    store = InMemoryPersonaStore(embedder)
    persona.set_store(store)
    persona.set_embedder(embedder)
    _stub_persona_judge.contradicts("Is an engineering student", "Is a physics student")
    _stub_persona_judge.contradicts("Is a physics student", "Is a music student")
    yield store
    persona.set_store(None)
    persona.set_embedder(None)


def say(text, when):
    return persona.remember(USER, Fact(text=text, category=["identity"], metadata={"source": "stated"}),
                            stated_at=when)


def test_a_replacing_statement_is_a_new_belief_with_its_own_date(store):
    old = say("Is an engineering student", datetime.now(timezone.utc) - timedelta(days=3))
    old_created = persona.get(USER, old.fact_id).created_at

    new = say("Is a physics student", datetime.now(timezone.utc))

    assert new.action == RememberAction.REPLACED
    assert new.fact_id != old.fact_id
    assert [s.id for s in new.superseded] == [old.fact_id]
    assert [f.text for f in persona.all_facts(USER)] == ["Is a physics student"]
    assert persona.get(USER, new.fact_id).created_at > old_created


def test_a_map_edit_that_contradicts_keeps_its_id(store):
    fact_id = say("Is an engineering student", datetime.now(timezone.utc) - timedelta(days=3)).fact_id
    current = persona.get(USER, fact_id)

    edited = persona.remember(USER, current.model_copy(update={"text": "Is a physics student"}),
                              stated_at=datetime.now(timezone.utc))

    assert edited.fact_id == fact_id
    assert persona.get(USER, fact_id).created_at == current.created_at
