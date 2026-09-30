"""Onboarding answers, the profile, and
what the answers set going - Persona facts, starting gains, estimator inputs.

The /me routes run on a bare FastAPI app with current_user overridden, like the
notes tests; Persona and profiles are in memory, and the tool_gain table is a
dict keyed by user, as in test_tenancy.py.
"""
import uuid
from datetime import datetime, time, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import me as me_api
from app.control.gain.gain_store import GainStore
from app.control.observer import observe
from app.core.auth import AuthUser, current_user
from app.schemas.event import VoiceEvent
from app.schemas.profile import OnboardingAnswers
from app.schemas.user_state import UserState
from app.store import persona, profile
from app.store.persona import FakeEmbedder, InMemoryPersonaStore
from app.store.profile import InMemoryProfileStore
from app.tools.functions.notification_batcher import Mode

A = uuid.UUID("aaaaaaaa-0000-0000-0000-00000000000a")
B = uuid.UUID("bbbbbbbb-0000-0000-0000-00000000000b")

EVERYTHING = {
    "campus_days": ["mon", "thu"],
    "class_times": ["morning", "afternoon"],
    "timetable_in_calendar": "partly",
    "sleep_start": "22:30",
    "sleep_end": "06:30",
    "travel_mode": "walking",
    "focus_interruptions": "urgent_only",
    "proactivity": "only_when_asked",
}


@pytest.fixture
def client(monkeypatch):
    embedder = FakeEmbedder()
    persona.set_store(InMemoryPersonaStore(embedder))
    persona.set_embedder(embedder)
    profile.set_store(InMemoryProfileStore())

    gains: dict[str, dict[str, dict]] = {}
    monkeypatch.setattr(GainStore, "_read_live", lambda self: dict(gains.get(self.user_id, {})))
    monkeypatch.setattr(GainStore, "save", lambda self, gain: gains.setdefault(self.user_id, {}).update(
        {gain.name: {"value": gain.value, "override": gain.override}}))

    app = FastAPI()
    app.include_router(me_api.router)
    acting = {"user": A}
    app.dependency_overrides[current_user] = lambda: AuthUser(id=acting["user"], email="a@example.com")
    test_client = TestClient(app)
    test_client.act_as = lambda user_id: acting.update(user=user_id)
    test_client.gains = gains
    yield test_client

    persona.set_store(None)
    persona.set_embedder(None)
    profile.set_store(None)


def onboarding_facts(user_id):
    return {f.metadata["question_id"]: f.text for f in persona.all_facts(user_id)
            if f.metadata.get("origin") == "onboarding"}


# --- GET /me and POST /me/onboarding ----------------------------------------------

def test_no_profile_before_onboarding(client):
    assert client.get("/me").json()["profile"] is None


def test_onboarding_is_saved_and_returned_by_me(client):
    r = client.post("/me/onboarding", json={"display_name": " Riley ", "answers": EVERYTHING})
    assert r.status_code == 200
    me = client.get("/me").json()["profile"]
    assert me["display_name"] == "Riley"
    assert me["onboarding_version"] == 1
    assert me["onboarding_completed_at"] is not None
    assert me["answers"]["campus_days"] == ["mon", "thu"]
    assert me["answers"]["sleep_start"] == "22:30:00"


def test_skipping_everything_still_completes_onboarding(client):
    r = client.post("/me/onboarding", json={})
    assert r.status_code == 200
    assert r.json()["onboarding_completed_at"] is not None
    assert onboarding_facts(A) == {}
    assert client.gains == {}


def test_unknown_answers_are_refused(client):
    assert client.post("/me/onboarding", json={"answers": {"campus_days": ["someday"]}}).status_code == 422
    assert client.post("/me/onboarding", json={"answers": {"favourite_colour": "red"}}).status_code == 422


def test_profiles_are_per_user(client):
    client.post("/me/onboarding", json={"display_name": "Riley", "answers": EVERYTHING})
    client.act_as(B)
    assert client.get("/me").json()["profile"] is None
    assert onboarding_facts(B) == {}


# --- Persona ----------------------------------------------------------------------

def test_answers_become_stated_facts(client):
    client.post("/me/onboarding", json={"answers": EVERYTHING})
    facts = onboarding_facts(A)
    assert facts["campus_days"] == "Is on campus on Monday and Thursday."
    assert facts["class_times"] == "Usually has classes in the morning and the afternoon."
    assert facts["sleep"] == "Usually sleeps from 22:30 to 06:30."
    assert facts["travel_mode"] == "Usually gets to uni on foot."
    assert "timetable_in_calendar" not in facts
    assert all(f.metadata["source"] == "stated" for f in persona.all_facts(A))


def test_repeating_onboarding_does_not_duplicate_facts(client):
    client.post("/me/onboarding", json={"answers": EVERYTHING})
    client.post("/me/onboarding", json={"answers": EVERYTHING})
    assert len(persona.all_facts(A)) == len(onboarding_facts(A))


def test_editing_an_answer_rewrites_its_fact(client):
    client.post("/me/onboarding", json={"answers": EVERYTHING})
    before = {f.metadata["question_id"]: f.id for f in persona.all_facts(A)}
    r = client.patch("/me/profile", json={"answers": {"travel_mode": "transit"}})
    assert r.status_code == 200
    assert r.json()["answers"]["campus_days"] == ["mon", "thu"]  # the rest kept
    assert onboarding_facts(A)["travel_mode"] == "Usually gets to uni by public transport."
    after = {f.metadata["question_id"]: f.id for f in persona.all_facts(A)}
    assert after["travel_mode"] == before["travel_mode"]  # updated in place


def test_clearing_an_answer_deletes_its_fact(client):
    client.post("/me/onboarding", json={"answers": EVERYTHING})
    client.patch("/me/profile", json={"answers": {"travel_mode": None}})
    assert "travel_mode" not in onboarding_facts(A)


def test_a_deleted_fact_stays_deleted_until_the_answer_changes(client):
    client.post("/me/onboarding", json={"answers": EVERYTHING})
    fact_id = next(f.id for f in persona.all_facts(A) if f.metadata["question_id"] == "campus_days")
    persona.delete(A, fact_id)

    client.post("/me/onboarding", json={"answers": EVERYTHING})              # same answer again
    client.patch("/me/profile", json={"answers": {"travel_mode": "driving"}})  # a different one
    assert "campus_days" not in onboarding_facts(A)

    client.patch("/me/profile", json={"answers": {"campus_days": ["tue"]}})
    assert onboarding_facts(A)["campus_days"] == "Is on campus on Tuesday."


def test_patching_the_name_leaves_the_answers(client):
    client.post("/me/onboarding", json={"display_name": "Riley", "answers": EVERYTHING})
    r = client.patch("/me/profile", json={"display_name": "Ri"})
    assert r.json()["display_name"] == "Ri"
    assert r.json()["answers"]["travel_mode"] == "walking"
    r = client.patch("/me/profile", json={"display_name": None})
    assert r.json()["display_name"] is None


def test_a_bad_patch_is_422_and_changes_nothing(client):
    client.post("/me/onboarding", json={"answers": EVERYTHING})
    assert client.patch("/me/profile", json={"answers": {"travel_mode": "teleport"}}).status_code == 422
    assert client.get("/me").json()["profile"]["answers"]["travel_mode"] == "walking"


# --- gains ------------------------------------------------------------------------

def test_only_when_asked_silences_every_tool(client):
    client.post("/me/onboarding", json={"answers": {"proactivity": "only_when_asked"}})
    dials = client.gains[str(A)]
    assert dials and all(d["override"] == 0.0 for d in dials.values())


def test_proactive_raises_the_learned_value(client):
    client.post("/me/onboarding", json={"answers": {"proactivity": "proactive"}})
    dials = client.gains[str(A)]
    assert dials and all(d["value"] >= profile.PROACTIVE_GAIN and d["override"] is None
                         for d in dials.values())


def test_gains_are_seeded_once(client):
    client.post("/me/onboarding", json={"answers": {"proactivity": "only_when_asked"}})
    tool = next(iter(client.gains[str(A)]))
    client.gains[str(A)][tool]["override"] = 0.8  # the user retunes a dial
    client.post("/me/onboarding", json={"answers": {"proactivity": "only_when_asked"}})
    client.patch("/me/profile", json={"answers": {"proactivity": "proactive"}})
    assert client.gains[str(A)][tool]["override"] == 0.8


# --- estimator inputs ---------------------------------------------------------------

def local(weekday_date, hh, mm=0):
    return datetime.combine(weekday_date, time(hh, mm))


MONDAY = datetime(2026, 9, 28).date()


def test_features_when_nothing_was_answered_match_the_old_behaviour():
    f = OnboardingAnswers().to_features(local(MONDAY, 12))
    assert f["calendar_reliability"] == 1.0
    assert f["focus_tolerance"] == 0.5
    assert f["campus_today"] == 0.0 and f["class_time_now"] == 0.0
    # 23:00-06:00, like QUIET_HOURS
    assert OnboardingAnswers().to_features(local(MONDAY, 23))["in_sleep_window"] == 1.0
    assert OnboardingAnswers().to_features(local(MONDAY, 5, 59))["in_sleep_window"] == 1.0
    assert OnboardingAnswers().to_features(local(MONDAY, 6))["in_sleep_window"] == 0.0


def test_features_from_answers():
    answers = OnboardingAnswers.model_validate(EVERYTHING)
    f = answers.to_features(local(MONDAY, 10))
    assert f["campus_today"] == 1.0
    assert f["class_time_now"] == 1.0
    assert f["calendar_reliability"] == 0.5
    assert f["travel_walking"] == 1.0 and f["travel_transit"] == 0.0
    assert f["focus_tolerance"] == 0.0
    assert answers.to_features(local(MONDAY + timedelta(days=1), 19))["campus_today"] == 0.0
    assert answers.to_features(local(MONDAY, 22, 45))["in_sleep_window"] == 1.0
    assert answers.to_features(local(MONDAY, 6, 15))["in_sleep_window"] == 1.0
    assert answers.to_features(local(MONDAY, 6, 30))["in_sleep_window"] == 0.0


def event_at(local_dt, offset_minutes=600):
    utc = (local_dt - timedelta(minutes=offset_minutes)).replace(tzinfo=timezone.utc)
    return VoiceEvent(id=uuid.uuid4(), timestamp=utc, text="hi")


def test_observe_is_in_sleep_mode_inside_the_users_own_window():
    """A night owl's 01:30 is SLEEP only with the
    default window; their own window moves it."""
    state = UserState(utc_offset_minutes=600)
    owl = OnboardingAnswers(sleep_start=time(2, 0), sleep_end=time(10, 0))

    at_0130 = local(MONDAY, 1, 30)
    assert observe(event_at(at_0130), state).mode is Mode.SLEEP
    assert observe(event_at(at_0130), state, priors=owl.to_features(at_0130)).mode is not Mode.SLEEP

    at_0800 = local(MONDAY, 8)
    assert observe(event_at(at_0800), state).mode is not Mode.SLEEP
    assert observe(event_at(at_0800), state, priors=owl.to_features(at_0800)).mode is Mode.SLEEP


def test_focus_answer_scales_in_class_interruptibility():
    state = UserState(utc_offset_minutes=600, calendar_ctx="in_event")
    when = local(MONDAY, 11)

    def budget(focus):
        answers = OnboardingAnswers(focus_interruptions=focus)
        return observe(event_at(when), state, priors=answers.to_features(when)).interruptibility

    default = observe(event_at(when), state).interruptibility
    assert budget("urgent_only") < budget("important") == default < budget("anything_useful")
