"""Each account's data is its own.

Two signed-in users, A and B, against the real main.py app. Tokens are signed
with a throwaway ES256 key, as in test_auth.py; Persona and the episode log are
the in-memory fakes from notes_support; the tool_gain table is a dict keyed by
user. What is checked: episodes, audit, Persona list/graph/
search, fact edits and deletes, outcomes, paused sessions, gains, consolidation
and the confirmation thread - none of it crosses from one account to the other.
"""
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from notes_support import FakeMemory

from app import intent_surface
from app.control.gain.gain_store import GainStore
from app.core import auth
from app.core.config import settings
from app.store import consolidation, memory, persona
from app.store.consolidation import StatedFact
from app.store.persona import FakeEmbedder, InMemoryPersonaStore

A = str(uuid.uuid4())
B = str(uuid.uuid4())
_KEY = ec.generate_private_key(ec.SECP256R1())


class _Jwks:
    def get_signing_key_from_jwt(self, token):
        return jwt.PyJWK.from_dict(json.loads(jwt.algorithms.ECAlgorithm.to_jwk(_KEY.public_key())))


def as_(user_id):
    token = jwt.encode(
        {"sub": user_id, "aud": "authenticated", "role": "authenticated", "exp": int(time.time()) + 3600},
        _KEY, algorithm="ES256", headers={"kid": "test"},
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    for name in ("SUPABASE_JWT_SECRET", "NOVA_AUTH_DISABLED", "K_SERVICE"):
        monkeypatch.delenv(name, raising=False)
    settings.cache_clear()
    monkeypatch.setattr(auth, "_jwks_client", lambda: _Jwks())
    monkeypatch.setenv("NOVA_MOCK_LLM", "1")
    monkeypatch.setattr(intent_surface, "MOCK_LLM", True)

    embedder = FakeEmbedder()
    persona.set_store(InMemoryPersonaStore(embedder))
    persona.set_embedder(embedder)

    log = FakeMemory()
    # All of it, including the incremental reads consolidation's "due?" check
    # makes on every /event - otherwise those reach for a real Supabase.
    for name in ("append", "get", "recent", "recent_all", "all", "since", "count_since", "close"):
        monkeypatch.setattr(memory, name, getattr(log, name))

    # The tool_gain table: {user_id: {tool_name: {value, override}}}.
    table: dict[str, dict[str, dict]] = {}
    monkeypatch.setattr(GainStore, "_read_live", lambda self: dict(table.get(self.user_id, {})))
    monkeypatch.setattr(GainStore, "save", lambda self, gain: table.setdefault(self.user_id, {}).update(
        {gain.name: {"value": gain.value, "override": gain.override}}))

    from app import main
    monkeypatch.setattr(main, "_API_KEY", "")
    client = TestClient(main.app)
    yield {"client": client, "log": log, "gains": table}

    persona.set_store(None)
    persona.set_embedder(None)
    intent_surface._PENDING_SESSIONS.clear()
    intent_surface._PENDING_CONFIRMATION.clear()
    settings.cache_clear()


def a_fact(user_id, text="likes bagels", **metadata):
    return persona.upsert(user_id, persona.Fact(text=text, category=["opinions", "likes"], metadata=metadata))


def an_episode_that_ran(log, user_id, tool):
    return log.append(user_id, {
        "event_type": "voice",
        "event": {"type": "voice", "text": "do it"},
        "action": {"actions": [{"tool": tool, "input": {}, "trigger": "requested", "ran": True}],
                   "speech": "Done."},
    })


def a_tool(client):
    return client.get("/tools/gain", headers=as_(A)).json()[0]["name"]


# --- episodes and the audit log ---------------------------------------------

def test_an_event_is_logged_under_its_sender(world):
    event = {"type": "voice", "id": str(uuid.uuid4()),
             "timestamp": datetime.now(timezone.utc).isoformat(), "text": "hello"}
    r = world["client"].post("/event", json={"event": event, "user_state": {}}, headers=as_(A))
    assert r.status_code == 200
    assert [row["user_id"] for row in world["log"].rows] == [A]


def test_audit_shows_only_your_own_episodes(world):
    an_episode_that_ran(world["log"], A, "set_reminder")
    client = world["client"]
    assert len(client.get("/audit", headers=as_(A)).json()) == 1
    assert client.get("/audit", headers=as_(B)).json() == []


# --- Persona ------------------------------------------------------------------

def test_persona_list_graph_and_search_are_separate(world):
    a_fact(A)
    client = world["client"]
    assert [f["text"] for f in client.get("/persona", headers=as_(A)).json()] == ["likes bagels"]
    assert client.get("/persona", headers=as_(B)).json() == []
    assert client.get("/persona/graph", headers=as_(B)).json()["stats"]["facts"] == 0
    assert persona.search(B, persona.PersonaQuery(text="likes bagels")) == []
    assert len(persona.search(A, persona.PersonaQuery(text="likes bagels"))) == 1


def test_editing_someone_elses_fact_is_404_and_changes_nothing(world):
    fact_id = a_fact(A)
    r = world["client"].patch(f"/persona/{fact_id}", json={"text": "hates bagels"}, headers=as_(B))
    assert r.status_code == 404
    assert persona.get(A, fact_id).text == "likes bagels"
    assert persona.all_facts(B) == []


def test_upserting_by_someone_elses_id_cannot_take_it_over(world):
    fact_id = a_fact(A)
    with pytest.raises(persona.FactNotFound):
        persona.upsert(B, persona.Fact(id=fact_id, text="mine now", category=["x"]))
    assert persona.get(A, fact_id).text == "likes bagels"


def test_deleting_someone_elses_fact_leaves_it_alone(world):
    fact_id = a_fact(A, signal="place", value="bagel shop")
    assert world["client"].delete(f"/persona/{fact_id}", headers=as_(B)).status_code == 204
    assert persona.get(A, fact_id).text == "likes bagels"
    assert persona.forgotten(A) == set() and persona.forgotten(B) == set()


def test_tombstones_are_per_user(world):
    fact_id = a_fact(A, signal="place", value="bagel shop")
    world["client"].delete(f"/persona/{fact_id}", headers=as_(A))
    assert persona.forgotten(A) == {"trend:place:bagel shop"}
    assert persona.forgotten(B) == set()


# --- gains --------------------------------------------------------------------

def test_gains_are_independent(world):
    client = world["client"]
    tool = a_tool(client)
    before = next(g for g in client.get("/tools/gain", headers=as_(B)).json() if g["name"] == tool)

    r = client.put(f"/tools/gain/{tool}", json={"override": 0.0}, headers=as_(A))
    assert r.status_code == 200 and r.json()["override"] == 0.0

    after = next(g for g in client.get("/tools/gain", headers=as_(B)).json() if g["name"] == tool)
    assert after == before
    assert set(world["gains"]) == {A}
    # And the shared registry's starting point didn't move either.
    assert intent_surface._REGISTRY.get_gain(tool).override is None


def test_an_outcome_on_someone_elses_episode_moves_nothing(world):
    client = world["client"]
    tool = a_tool(client)
    episode_id = an_episode_that_ran(world["log"], A, tool)

    assert client.post("/event/outcome", json={"episode_id": episode_id, "outcome": "accepted"},
                       headers=as_(B)).status_code == 204
    assert world["gains"] == {}
    assert world["log"].get(A, episode_id)["outcome"] is None

    client.post("/event/outcome", json={"episode_id": episode_id, "outcome": "accepted"}, headers=as_(A))
    assert tool in world["gains"][A]
    assert world["log"].get(A, episode_id)["outcome"] == "accepted"


# --- the conversation in flight -------------------------------------------------

def test_continuing_someone_elses_session_is_404(world):
    intent_surface._PENDING_SESSIONS["s1"] = {
        "user_id": A, "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "messages": [], "tool_use_id": "t1", "event_id": uuid.uuid4(), "ctx": None,
    }
    r = world["client"].post("/event/continue", json={"session_id": "s1", "result": {}}, headers=as_(B))
    assert r.status_code == 404
    assert "s1" in intent_surface._PENDING_SESSIONS  # still there for A


def test_an_expired_session_is_404(world):
    intent_surface._PENDING_SESSIONS["s1"] = {
        "user_id": A, "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
        "messages": [], "tool_use_id": "t1", "event_id": uuid.uuid4(), "ctx": None,
    }
    r = world["client"].post("/event/continue", json={"session_id": "s1", "result": {}}, headers=as_(A))
    assert r.status_code == 404


def test_the_confirmation_thread_is_not_shared(world):
    thread = [{"role": "user", "content": "A's calendar"}, {"role": "assistant", "content": "Shall I?"}]
    intent_surface._stash_pending_confirmation(A, thread)
    assert intent_surface._pop_pending_confirmation(B) is None
    assert intent_surface._pop_pending_confirmation(A) == thread


# --- consolidation --------------------------------------------------------------

def greedy_extractor(batch):
    return [StatedFact(text=u["text"], category=["facts"], episode_id=u["id"]) for u in batch]


def test_consolidation_reads_only_the_callers_history(world):
    world["log"].append(A, {"event_type": "voice", "event": {"type": "voice", "text": "I study law"}})
    assert consolidation.consolidate_statements(B, extractor=greedy_extractor) == []
    assert persona.all_facts(B) == []

    written = consolidation.consolidate_statements(A, extractor=greedy_extractor)
    assert [f.text for f in written] == ["I study law"]
    assert [f.text for f in persona.all_facts(A)] == ["I study law"]
