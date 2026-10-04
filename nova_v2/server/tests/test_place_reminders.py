"""Place reminders: "remind me to buy milk when I get to the shops".

places.resolve turns a named place into geofence circles (the Places API is
faked here through places._post), set_reminder / update_reminder validate and
carry them, and intent_surface._run_local_tool hands the user's location to the
search without ever recording it.
"""
from datetime import datetime, timezone

import pytest

from app import intent_surface
from app.control.commands import Command
from app.control.controller import ProportionalController
from app.control.observer import Observation
from app.tools.core import narration
from app.tools.functions import places, reminder_tool
from app.tools.functions.reminder_tool import SetReminderTool, UpdateReminderTool

NEAR = "-35.2777,149.1185"
# 2026-09-23 06:00 UTC is 16:00 in Canberra (AEST, +600).
NOW_UTC = datetime(2026, 9, 23, 6, 0, tzinfo=timezone.utc)
REMINDER_ID = "5b0f3c1e-2d4a-4c8e-9f1a-0123456789ab"


def place(name, lat, lng, ratings=0, viewport=None):
    p = {"displayName": {"text": name}, "location": {"latitude": lat, "longitude": lng},
         "userRatingCount": ratings}
    if viewport:
        p["viewport"] = viewport
    return p


@pytest.fixture
def api(monkeypatch):
    """Canned Places responses: `api.results` is what every search returns, and
    `api.calls` records (url, body)."""
    class Api:
        results: list = []
        calls: list = []
    monkeypatch.setattr(places, "MAPS_API_KEY", "test-key")

    def fake_post(url, body):
        Api.calls.append((url, body))
        return Api.results
    monkeypatch.setattr(places, "_post", fake_post)
    Api.results, Api.calls = [], []
    return Api


# --- places.resolve -----------------------------------------------------------

def test_here_is_the_users_location_without_a_search(api):
    points, problem = places.resolve("here", None, None, NEAR)
    assert problem is None
    assert points == [{"name": "Here", "lat": -35.2777, "lng": 149.1185, "radius_m": places.MIN_RADIUS_M}]
    assert api.calls == []


def test_here_without_a_location_fails():
    points, problem = places.resolve("here", None, None, None)
    assert points == [] and "don't know where the user is" in problem


def test_no_maps_key_fails(monkeypatch):
    monkeypatch.setattr(places, "MAPS_API_KEY", None)
    points, problem = places.resolve("one", "Woolworths Dickson", None, NEAR)
    assert points == [] and "Maps key" in problem


def test_one_takes_the_top_result_biased_near_the_user(api):
    api.results = [place("Woolworths Dickson", -35.25, 149.14)]
    points, problem = places.resolve("one", "Woolworths Dickson", None, NEAR)
    assert problem is None
    assert [p["name"] for p in points] == ["Woolworths Dickson"]
    body = api.calls[0][1]
    assert body["textQuery"] == "Woolworths Dickson" and body["pageSize"] == 1
    assert body["locationBias"]["circle"]["center"] == {"latitude": -35.2777, "longitude": 149.1185}


def test_one_still_works_without_a_location(api):
    """An address is findable without knowing where the user is."""
    api.results = [place("12 Smith St", -35.25, 149.14)]
    points, problem = places.resolve("one", "12 Smith St Ainslie", None, None)
    assert problem is None and len(points) == 1
    assert "locationBias" not in api.calls[0][1]


def test_one_with_no_match_asks_again(api):
    points, problem = places.resolve("one", "zzqq", None, NEAR)
    assert points == [] and "Couldn't find" in problem


def test_a_failed_request_is_not_no_match(api, monkeypatch):
    monkeypatch.setattr(places, "_post", lambda url, body: None)
    points, problem = places.resolve("one", "Woolworths", None, NEAR)
    assert points == [] and "failed" in problem


def test_a_big_place_gets_a_wider_circle(api):
    # About 1.1 km tall and wide.
    viewport = {"low": {"latitude": -35.285, "longitude": 149.11},
                "high": {"latitude": -35.275, "longitude": 149.122}}
    api.results = [place("The Australian National University", -35.28, 149.116, viewport=viewport)]
    [point], _ = places.resolve("one", "ANU", None, NEAR)
    assert 500 < point["radius_m"] <= places.MAX_RADIUS_M


def test_chain_keeps_only_branches_of_the_brand(api):
    api.results = [
        place("Eg Ampol Belconnen", -35.24, 149.07),
        place("Woolworths Dickson", -35.25, 149.14),
        place("Coles Canberra Civic", -35.28, 149.13),
        place("Woolworths Metro - Wright", -35.32, 149.03),
    ]
    points, problem = places.resolve("chain", "Woolworths", None, NEAR)
    assert problem is None
    assert [p["name"] for p in points] == ["Woolworths Dickson", "Woolworths Metro - Wright"]


def test_chain_name_match_ignores_case_and_punctuation(api):
    api.results = [place("McDonald's Braddon", -35.27, 149.13)]
    points, _ = places.resolve("chain", "mcdonalds", None, NEAR)
    assert len(points) == 1


def test_chain_needs_a_location(api):
    points, problem = places.resolve("chain", "Woolworths", None, None)
    assert points == [] and "one specific place" in problem
    assert api.calls == []


def test_type_searches_nearby_by_google_type(api):
    api.results = [place("Lyneham Shops", -35.2519, 149.1251)]
    points, problem = places.resolve("type", None, "shops", NEAR)
    assert problem is None and len(points) == 1
    url, body = api.calls[0]
    assert url == places.NEARBY_SEARCH_URL
    assert body["includedTypes"] == ["shopping_mall"]
    assert body["rankPreference"] == "DISTANCE"


def test_unknown_type_is_rejected(api):
    points, problem = places.resolve("type", None, "casino", NEAR)
    assert points == [] and "place_type must be one of" in problem


def test_close_results_merge_into_one_place_named_after_the_best_known(api):
    """The Canberra Centre, its loading dock and its car park are one place."""
    api.results = [
        place("Big W - Loading Dock", -35.2800, 149.1335, ratings=3),
        place("Canberra Centre", -35.2802, 149.1332, ratings=12000),
        place("Canberra Centre Car Park", -35.2808, 149.1340, ratings=40),
        place("Lyneham Shops", -35.2519, 149.1251, ratings=300),
    ]
    points, _ = places.resolve("type", None, "shops", NEAR)
    assert [p["name"] for p in points] == ["Canberra Centre", "Lyneham Shops"]
    merged = points[0]
    # Wide enough to cover every member, plus the margin.
    for lat, lng in [(-35.2800, 149.1335), (-35.2802, 149.1332), (-35.2808, 149.1340)]:
        assert places.distance_m((merged["lat"], merged["lng"]), (lat, lng)) < merged["radius_m"]


def test_clusters_are_nearest_first_and_capped(api):
    api.results = [place(f"Shops {i}", -35.2777 - i * 0.01, 149.1185) for i in range(30, 0, -1)]
    points, _ = places.resolve("type", None, "shops", NEAR)
    assert len(points) == places.MAX_POINTS
    assert points[0]["name"] == "Shops 1"


def test_parse_latlng_rejects_junk():
    assert places.parse_latlng("-35.28,149.13") == (-35.28, 149.13)
    assert places.parse_latlng("nowhere") is None
    assert places.parse_latlng("91,0") is None
    assert places.parse_latlng(None) is None


# --- set_reminder -------------------------------------------------------------

@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch):
    monkeypatch.setattr(reminder_tool, "_now_utc", lambda: NOW_UTC)


@pytest.fixture
def resolved(monkeypatch):
    """places.resolve pinned to two Woolworths, recording what it was asked."""
    asked = []

    def fake_resolve(match, query, place_type, near):
        asked.append((match, query, place_type, near))
        return [
            {"name": "Woolworths Dickson", "lat": -35.25, "lng": 149.14, "radius_m": 150},
            {"name": "Woolworths Hawker", "lat": -35.24, "lng": 149.04, "radius_m": 150},
        ], None
    monkeypatch.setattr(places, "resolve", fake_resolve)
    return asked


SHOPS = {"on": "arrive", "match": "chain", "query": "Woolworths", "label": "Woolworths"}


def set_reminder(**kwargs):
    return SetReminderTool().invoke({"utc_offset_minutes": 600, **kwargs})


def test_set_with_a_place(resolved):
    result = set_reminder(text="Buy milk", place=SHOPS, near=NEAR)
    assert result["success"] is True
    assert result["place"]["on"] == "arrive"
    assert result["place"]["label"] == "Woolworths"
    assert len(result["place"]["points"]) == 2
    assert result["every_time"] is False
    assert result["found"] == "2 places, nearest first: Woolworths Dickson, Woolworths Hawker"
    assert resolved == [("chain", "Woolworths", None, NEAR)]


def test_set_every_time(resolved):
    result = set_reminder(text="Stretch", place={**SHOPS, "label": "the gym"}, every_time=True)
    assert result["success"] is True and result["every_time"] is True


def test_a_time_with_a_place_is_a_deadline(resolved):
    """ "At 5, or when I get to Woolworths, whichever comes first." """
    result = set_reminder(text="Buy milk", place=SHOPS, due_local="2026-09-23T17:00:00")
    assert result["success"] is True
    assert result["due_local"] == "2026-09-23T17:00:00"
    assert result["place"]["label"] == "Woolworths"


def test_a_passed_deadline_is_rejected_before_any_search(resolved):
    result = set_reminder(text="Buy milk", place=SHOPS, due_local="2026-09-23T09:00:00")
    assert result["success"] is False and "already passed" in result["error"]
    assert resolved == []


def test_after_local_bounds_when_it_can_go_off(resolved):
    """ "When I get to uni tomorrow." """
    result = set_reminder(text="Return the book", place=SHOPS, after_local="2026-09-24T00:00:00")
    assert result["success"] is True
    assert result["after_local"] == "2026-09-24T00:00:00"
    assert result["due_local"] is None


def test_deadline_must_come_after_after_local(resolved):
    result = set_reminder(text="x", place=SHOPS, after_local="2026-09-24T00:00:00",
                          due_local="2026-09-23T18:00:00")
    assert result["success"] is False and "must be after" in result["error"]


@pytest.mark.parametrize("after", ["tomorrow", "2026-09-24T00:00:00Z", ""])
def test_malformed_after_local_is_rejected(resolved, after):
    result = set_reminder(text="x", place=SHOPS, after_local=after)
    assert result["success"] is False and "after_local" in result["error"]


def test_after_local_needs_a_place():
    result = set_reminder(text="x", in_minutes=10, after_local="2026-09-24T00:00:00")
    assert result["success"] is False and "only for place" in result["error"]


def test_every_time_cannot_have_a_deadline(resolved):
    result = set_reminder(text="Stretch", place=SHOPS, every_time=True, in_minutes=30)
    assert result["success"] is False and "deadline" in result["error"]


def test_both_kinds_of_time_are_still_rejected(resolved):
    result = set_reminder(text="x", place=SHOPS, in_minutes=5, due_local="2026-09-23T17:00:00")
    assert result["success"] is False and "exactly one" in result["error"]


def test_place_with_recurrence_is_pointed_at_every_time(resolved):
    result = set_reminder(text="Stretch", place=SHOPS, recurrence={"frequency": "daily"})
    assert result["success"] is False and "every_time" in result["error"]


def test_every_time_needs_a_place():
    result = set_reminder(text="Stretch", in_minutes=10, every_time=True)
    assert result["success"] is False and "only for place" in result["error"]


@pytest.mark.parametrize("bad, why", [
    ({"on": "near", "match": "one", "query": "x", "label": "x"}, "place.on"),
    ({"on": "arrive", "match": "somewhere", "label": "x"}, "place.match"),
    ({"on": "arrive", "match": "one", "label": "home"}, "place.query"),
    ({"on": "arrive", "match": "type", "place_type": "casino", "label": "x"}, "place_type"),
    ({"on": "arrive", "match": "here", "label": " "}, "place.label"),
    ("the shops", "place must be an object"),
])
def test_malformed_place_is_rejected(resolved, bad, why):
    result = set_reminder(text="Buy milk", place=bad)
    assert result["success"] is False and why in result["error"]
    assert resolved == []


def test_a_place_that_cannot_be_found_is_rejected(monkeypatch):
    monkeypatch.setattr(places, "resolve", lambda *a: ([], "Couldn't find 'zzqq'."))
    result = set_reminder(text="Buy milk", place={**SHOPS, "match": "one", "query": "zzqq"})
    assert result == {"success": False, "error": "Couldn't find 'zzqq'."}


def test_here_is_found_as_where_the_user_is(monkeypatch):
    monkeypatch.setattr(places, "resolve", lambda *a: (
        [{"name": "Here", "lat": -35.2777, "lng": 149.1185, "radius_m": 150}], None))
    result = set_reminder(text="Water the plants",
                          place={"on": "arrive", "match": "here", "label": "here"})
    assert result["found"] == "where the user is now"


# --- update_reminder ----------------------------------------------------------

def update_reminder(**kwargs):
    base = {"reminder_id": REMINDER_ID, "label": "Buy milk", "utc_offset_minutes": 600, "action": "edit"}
    return UpdateReminderTool().invoke({**base, **kwargs})


def test_edit_moves_a_reminder_to_a_place(resolved):
    result = update_reminder(place={"on": "arrive", "match": "one", "query": "12 Smith St", "label": "home"})
    assert result["success"] is True
    assert result["place"]["label"] == "home"
    assert "found" in result


def test_edit_place_and_time_together_is_rejected(resolved):
    result = update_reminder(place=SHOPS, due_local="2026-09-23T16:30:00")
    assert result["success"] is False and "only one of" in result["error"]


def test_edit_can_change_only_every_time():
    result = update_reminder(every_time=True)
    assert result["success"] is True and result["every_time"] is True


def test_edit_every_time_must_be_a_bool():
    result = update_reminder(every_time="yes")
    assert result["success"] is False


# --- intent_surface plumbing --------------------------------------------------

@pytest.fixture
def ctx():
    turn = ProportionalController(intent_surface._REGISTRY).open_turn(Observation(), Command(text="do it"))
    return intent_surface.TurnContext(turn=turn, location_ctx=NEAR, utc_offset_minutes=600)


def test_the_search_gets_the_location_but_the_action_does_not(ctx, monkeypatch):
    seen = {}
    resolved_place = {"on": "arrive", "match": "type", "label": "the shops",
                      "points": [{"name": "Lyneham Shops", "lat": -35.25, "lng": 149.12, "radius_m": 150}]}

    def dispatch(name, tool_input):
        seen.update(tool_input)
        return {"success": True, "place": resolved_place, "every_time": False,
                "after_local": "2026-09-24T00:00:00", "found": "Lyneham Shops"}
    monkeypatch.setattr(intent_surface._DISPATCHER, "dispatch_reactive", dispatch)

    asked = {"on": "arrive", "match": "type", "place_type": "shops", "label": "the shops"}
    result = intent_surface._run_local_tool("set_reminder", {"text": "Buy milk", "place": asked}, ctx)

    assert seen["near"] == NEAR
    [action] = ctx.actions
    assert action.ran is True
    assert "near" not in action.input and "origin" not in action.input
    # The phone reads the resolved circles, not what the model asked for.
    assert action.input["place"] == resolved_place
    assert action.input["every_time"] is False
    assert action.input["after_local"] == "2026-09-24T00:00:00"
    # The model confirms from `found` and never sees coordinates.
    assert "place" not in result and result["found"] == "Lyneham Shops"


def test_a_timed_reminder_still_gets_no_location(ctx, monkeypatch):
    seen = {}
    monkeypatch.setattr(intent_surface._DISPATCHER, "dispatch_reactive",
                        lambda name, tool_input: seen.update(tool_input) or {"success": True})
    intent_surface._run_local_tool("set_reminder", {"text": "x", "in_minutes": 5}, ctx)
    assert "near" not in seen


def test_a_failed_place_lookup_is_not_run(ctx, monkeypatch):
    monkeypatch.setattr(intent_surface._DISPATCHER, "dispatch_reactive",
                        lambda name, tool_input: {"success": False, "error": "Couldn't find it."})
    intent_surface._run_local_tool(
        "set_reminder", {"text": "x", "place": {"on": "arrive", "match": "one", "query": "zz", "label": "zz"}}, ctx)
    assert [a.ran for a in ctx.actions] == [False]


# --- narration ----------------------------------------------------------------

@pytest.mark.parametrize("tool_input, expected", [
    ({"place": {"on": "arrive", "label": "the shops"}}, " for when you get to the shops"),
    ({"place": {"on": "leave", "label": "work"}}, " for when you leave work"),
    ({"place": {"on": "arrive", "label": "the gym"}, "every_time": True}, " for every time you get to the gym"),
    ({"place": {"on": "arrive", "label": "Woolworths"}, "in_minutes": 90},
     " for when you get to Woolworths, or in 1 hour 30 minutes at the latest"),
])
def test_narration_says_the_place(tool_input, expected):
    assert narration._reminder_when(tool_input) == expected
