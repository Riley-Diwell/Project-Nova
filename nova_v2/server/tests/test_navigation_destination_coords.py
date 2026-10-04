"""navigation_departure_time carries the route's end point when Directions gave one.

Android's DeviceCompass points the device at destination_lat/lng while the
departure is live. The arrived case has no departure at all.
"""
from app.tools.functions import navigation


def directions(distance_m=5000, end_location=None):
    leg = {
        "distance": {"value": distance_m},
        "duration": {"text": "20 mins", "value": 1200},
        "steps": [],
    }
    if end_location is not None:
        leg["end_location"] = end_location
    return {"routes": [{"legs": [leg]}]}


def test_directions_result_carries_end_location():
    result = navigation._directions_result(
        directions(end_location={"lat": -35.2777, "lng": 149.1185}),
        "Birch Bldg 35", "walking", None, minutes_until_start=45,
    )
    assert result["destination_lat"] == -35.2777
    assert result["destination_lng"] == 149.1185
    assert result["leave_in_minutes"] == 25.0


def test_directions_result_without_end_location_has_no_coords():
    result = navigation._directions_result(directions(), "ANU", "walking", None, minutes_until_start=45)
    assert "destination_lat" not in result
    assert "destination_lng" not in result


def test_arrived_has_no_coords():
    result = navigation._directions_result(
        directions(distance_m=50, end_location={"lat": -35.0, "lng": 149.0}),
        "ANU", "walking", None, minutes_until_start=45,
    )
    assert result["arrived"] is True
    assert "destination_lat" not in result
