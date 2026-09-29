"""Automatic consolidation: due when enough has happened, and each pass reads
only the episodes since the last - with the trend counts coming out exactly as
a full recount would."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import _trends_reference as reference
from notes_support import USER, FakeMemory

from app.core.request_user import as_user
from app.store import consolidation, memory, persona
from app.store.consolidation import DerivedFact, StatedFact, state
from app.store.consolidation.state import ConsolidationState, InMemoryStateStore, is_due
from app.store.consolidation.trends import (
    candidates_from_tallies,
    find_candidates,
    merge_tallies,
    tallies_from_json,
    tallies_to_json,
    tally_rows,
)
from app.store.persona import FakeEmbedder, InMemoryPersonaStore
from test_tenancy import A, as_, world  # noqa: F401 - world is a fixture

NOW = datetime.now(timezone.utc)


def at(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def nav(destination: str, minutes_ago: float, text: str = "take me there", offset: int = 600, **extra):
    return {
        "id": str(uuid.uuid4()), "created_at": at(minutes_ago), "event_type": "voice",
        "event": {"type": "voice", "text": text},
        "user_state": {"utc_offset_minutes": offset},
        "action": {"actions": [{"tool": "navigation_departure_time", "input": {"destination": destination},
                                "trigger": "requested", "ran": True}]},
        **extra,
    }


def notification(app: str, minutes_ago: float):
    return {"id": str(uuid.uuid4()), "created_at": at(minutes_ago), "event_type": "notification",
            "event": {"type": "notification", "app": app}, "user_state": {}, "action": None}


# A log with the awkward cases: spellings that fold together, a refused call,
# two calls in one turn, repeated phrasings, a row with no timestamp.
LOG = [
    nav("Brooklyn Boy Bagels", 5000, "the bagel place"),
    nav("brooklyn boy bagels, Fyshwick", 4000, "bagels please"),
    notification("Slack", 3900),
    nav("Brooklyn Boy Bagels", 3000, "the bagel place"),
    {**nav("Kambri car park", 2900), "action": {"actions": [
        {"tool": "navigation_departure_time", "input": {"destination": "Kambri car park"}, "ran": True},
        {"tool": "navigation_departure_time", "input": {"destination": "Kambri car park"}, "ran": True},
        {"tool": "navigation_departure_time", "input": {"destination": "Nowhere"}, "ran": False},
    ]}},
    notification("slack", 2000),
    nav("BROOKLYN BOY BAGELS", 1500, "one more bagel", offset=-300),
    notification("Slack", 1000),
    {**nav("Kambri car park", 900), "created_at": None},
    nav("Kambri Car Park", 800, "parking"),
]


# --- counting ------------------------------------------------------------------------

@pytest.mark.parametrize("split", range(len(LOG) + 1))
def test_tallies_of_two_spans_give_exactly_the_full_recount(split):
    expected = reference.find_candidates(LOG, min_support=2)
    merged = merge_tallies(tally_rows(LOG[:split]), tally_rows(LOG[split:]))
    stored = tallies_from_json(tallies_to_json(merged))       # through the database and back
    assert candidates_from_tallies(stored, min_support=2) == expected
    assert find_candidates(LOG, min_support=2) == expected


# --- when it's due --------------------------------------------------------------------

@pytest.mark.parametrize("last_run, new, running, expected", [
    (None, 0, False, "due"),                       # never run
    (timedelta(minutes=5), 50, False, "idle"),     # debounced
    (timedelta(hours=1), 19, False, "idle"),       # not enough yet
    (timedelta(hours=1), 20, False, "due"),        # enough
    (timedelta(hours=25), 1, False, "due"),        # a day, and something new
    (timedelta(hours=25), 0, False, "idle"),       # a day, but nothing new
    (None, 100, True, "running"),                  # a pass holds the lease
])
def test_the_due_rule(last_run, new, running, expected):
    current = ConsolidationState(
        last_run_at=NOW - last_run if last_run else None,
        lease_until=NOW + timedelta(minutes=1) if running else None,
    )
    assert is_due(current, new, NOW).status == expected


# --- a pass ------------------------------------------------------------------------------

class Phraser:
    def __init__(self):
        self.calls = []

    def __call__(self, candidates):
        self.calls.append([c.value for c in candidates])
        return [DerivedFact(text=f"Goes to {c.value}", category=["routines", "places"], candidate=c)
                for c in candidates]


def says(batch):
    """Extractor: every utterance starting "I " is a fact about the user."""
    return [StatedFact(text=u["text"], category=["facts"], episode_id=u["id"])
            for u in batch if u["text"].startswith("I ")]


@pytest.fixture
def pass_world(monkeypatch):
    embedder = FakeEmbedder()
    persona.set_store(InMemoryPersonaStore(embedder))
    persona.set_embedder(embedder)
    log = FakeMemory()
    for name in ("append", "get", "recent", "recent_all", "all", "since", "count_since", "close"):
        monkeypatch.setattr(memory, name, getattr(log, name))
    store = InMemoryStateStore()
    state.set_state_store(store)
    with as_user(USER):
        yield SimpleNamespace(log=log, state=store, phraser=Phraser())
    state.set_state_store(None)
    persona.set_store(None)
    persona.set_embedder(None)


def add(w, row):
    w.log.rows.append({**row, "user_id": str(USER)})


def run(w, **kw):
    return consolidation.consolidate_incremental(USER, phraser=w.phraser, extractor=says, **kw)


def facts():
    return sorted(f.text for f in persona.all_facts(USER))


def test_first_pass_reads_the_log_once_then_only_whats_new(pass_world):
    w = pass_world
    for m in (300, 200, 100):
        add(w, nav("The Gym", m))
    add(w, {**notification("Slack", 90), "event_type": "voice", "event": {"type": "voice", "text": "I study engineering"}})

    check = consolidation.due(USER, fresh=True)
    assert check.due                                           # never run
    first = consolidation.run_if_due(USER, phraser=w.phraser, extractor=says)
    assert first.ran and first.episodes_read == 4
    assert facts() == ["Goes to The Gym", "I study engineering"]
    assert getattr(w.log, "full_reads", 0) == 0                # never read the whole log

    add(w, nav("The Gym", 50))
    assert not consolidation.run_if_due(USER).ran              # just ran: debounced
    again = run(w)
    assert again.episodes_read == 1
    assert [d.outcome for d in again.derived] == ["updated"]
    assert w.phraser.calls == [["The Gym"]]                    # held: not re-phrased
    [gym] = [f for f in persona.all_facts(USER) if f.text == "Goes to The Gym"]
    assert gym.metadata["support"] == 4


def test_a_trend_nothing_new_touched_is_left_alone(pass_world):
    w = pass_world
    for m in (300, 200, 100):
        add(w, nav("The Gym", m))
    run(w)
    [gym] = persona.all_facts(USER)
    add(w, notification("Slack", 50))
    run(w)
    assert persona.get(USER, gym.id).updated_at == gym.updated_at


def test_a_pass_that_dies_resumes_without_counting_twice(pass_world, monkeypatch):
    w = pass_world
    for m in (500, 400, 300, 200):
        add(w, nav("The Gym", m))
    monkeypatch.setattr(consolidation, "CHUNK", 2)
    boom = {"after": 1}

    def flaky(batch):
        boom["after"] -= 1
        if boom["after"] < 0:
            raise RuntimeError("model down")
        return []

    with pytest.raises(RuntimeError):
        consolidation.consolidate_incremental(USER, phraser=w.phraser, extractor=flaky)
    assert w.state.get(USER).lease_holder is None              # lease released

    run(w)
    tallies = tallies_from_json(w.state.get(USER).tallies)
    assert sum(t.support for t in tallies.values()) == 4       # every episode, once


def test_episodes_sharing_a_timestamp_are_each_read_once(pass_world, monkeypatch):
    w = pass_world
    same = at(100)
    for _ in range(3):
        add(w, {**nav("The Gym", 0), "created_at": same})
    monkeypatch.setattr(consolidation, "CHUNK", 1)
    run(w)
    run(w)
    tallies = tallies_from_json(w.state.get(USER).tallies)
    assert sum(t.support for t in tallies.values()) == 3


def test_episodes_still_settling_wait_for_the_next_pass(pass_world):
    w = pass_world
    add(w, nav("The Gym", 1))                                  # a minute old: its turn may not have closed
    assert run(w).episodes_read == 0


def test_a_running_pass_blocks_another(pass_world):
    w = pass_world
    assert w.state.claim(USER, "someone-else", timedelta(minutes=5))
    result = run(w)
    assert not result.ran and result.running


def test_a_new_tally_version_recounts_from_the_start(pass_world, monkeypatch):
    w = pass_world
    for m in (300, 200, 100):
        add(w, nav("The Gym", m))
    run(w)
    monkeypatch.setattr(consolidation, "TALLY_VERSION", 99)
    assert run(w).episodes_read == 3
    assert w.state.get(USER).tally_version == 99


# --- over HTTP ------------------------------------------------------------------------------

@pytest.fixture
def http(world):
    store = InMemoryStateStore()
    state.set_state_store(store)
    yield world
    state.set_state_store(None)


def test_event_replies_say_when_a_pass_is_due(http):
    event = {"type": "voice", "id": str(uuid.uuid4()), "timestamp": NOW.isoformat(), "text": "hello"}
    body = http["client"].post("/event", json={"event": event, "user_state": {}}, headers=as_(A)).json()
    assert body["consolidation_due"] is True                   # never run yet


def test_the_phone_asks_and_the_server_runs_only_when_due(http):
    client = http["client"]
    first = client.post("/persona/consolidate?if_due=true", headers=as_(A)).json()
    assert first["ran"] is True
    second = client.post("/persona/consolidate?if_due=true", headers=as_(A)).json()
    assert second["ran"] is False and second["last_run_at"]

    graph = client.get("/persona/graph", headers=as_(A))
    assert graph.headers["X-Nova-Consolidation"] == "idle"
    assert "X-Nova-Last-Consolidated" in graph.headers
    again = client.get("/persona/graph", headers={**as_(A), "If-None-Match": graph.headers["ETag"]})
    assert again.status_code == 304 and again.headers["X-Nova-Consolidation"] == "idle"


def test_without_the_state_table_nothing_is_reported_due(world):
    state.set_state_store(None)
    event = {"type": "voice", "id": str(uuid.uuid4()), "timestamp": NOW.isoformat(), "text": "hello"}
    body = world["client"].post("/event", json={"event": event, "user_state": {}}, headers=as_(A)).json()
    assert body["consolidation_due"] is False
