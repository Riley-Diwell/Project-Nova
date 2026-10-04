"""navigation_departure_time without a hardcoded travel table.

error() ranks urgency by how soon a located commitment starts, so the real
Directions call runs for any place, not just names a table knew. When no
travel time can be measured, the tool says so with travel_unknown, and the
Intent Surface hands that to the phone as departure_unknown.
"""
from types import SimpleNamespace

import pytest

from app import intent_surface
from app.tools.functions import navigation


def observation(minutes, location="Coffee Club, Braddon"):
    commitment = SimpleNamespace(location=location, minutes_until_start=minutes)
    return SimpleNamespace(next_commitment=commitment)


@pytest.mark.parametrize("minutes, expected", [
    (90, 0.0), (60, 0.0), (45, 0.5), (30, 1.0), (5, 1.0),
])
def test_error_ramps_on_minutes_until_start(minutes, expected):
    assert navigation.NavigationTool().error(observation(minutes)) == expected


def test_error_measures_places_the_old_table_never_knew():
    # The old table returned None (open-loop) for anything not named like uni.
    assert navigation.NavigationTool().error(observation(20, "Dentist, 12 Smith St")) == 1.0


def test_error_is_zero_without_a_location_or_commitment():
    tool = navigation.NavigationTool()
    assert tool.error(observation(10, location=None)) == 0.0
    assert tool.error(SimpleNamespace(next_commitment=None)) == 0.0


def test_no_maps_key_says_it_cannot_work_it_out(monkeypatch):
    monkeypatch.setattr(navigation, "MAPS_API_KEY", None)
    result = navigation.NavigationTool()._execute(
        {"destination": "ANU", "origin": "-35.28,149.13", "minutes_until_start": 45})
    assert result["success"] is False
    assert result["travel_unknown"] is True
    assert "leave_in_minutes" not in result
    assert "duration" not in result


def test_maps_failure_says_it_cannot_work_it_out(monkeypatch):
    monkeypatch.setattr(navigation, "MAPS_API_KEY", "key")

    def boom(*args, **kwargs):
        raise ConnectionError("down")

    monkeypatch.setattr(navigation.requests, "get", boom)
    result = navigation.NavigationTool()._execute(
        {"destination": "ANU", "origin": "-35.28,149.13", "minutes_until_start": 45})
    assert result["travel_unknown"] is True
    assert result["reason"] == "maps unavailable"


def test_no_origin_is_travel_unknown():
    result = navigation.NavigationTool()._execute({"destination": "ANU"})
    assert result["travel_unknown"] is True
    assert result["needs_location"] is True


def test_turn_context_and_results_carry_departure_unknown():
    assert "departure_unknown" in intent_surface.TurnContext.__dataclass_fields__
    assert "departure_unknown" in intent_surface.IntentResult.model_fields
