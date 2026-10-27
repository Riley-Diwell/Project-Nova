"""navigation_departure_time carries the route's end point when Routes gave one.

Android's DeviceCompass points the device at destination_lat/lng while the
departure is live. The arrived case has no departure at all.
"""
from app.tools.functions import navigation


def leg(distance_m=5000, end_location=None):
    leg = {
        "distanceMeters": distance_m,
        "duration": "1200s",
        "localizedValues": {"duration": {"text": "20 mins"}},
        "steps": [],
    }
    if end_location is not None:
        leg["endLocation"] = {"latLng": end_location}
    return leg


def test_directions_result_carries_end_location():
    result = navigation._directions_result(
        leg(end_location={"latitude": -35.2777, "longitude": 149.1185}),
        "Birch Bldg 35", "walking", None, minutes_until_start=45,
    )
    assert result["destination_lat"] == -35.2777
    assert result["destination_lng"] == 149.1185
    assert result["leave_in_minutes"] == 25.0


def test_directions_result_without_end_location_has_no_coords():
    result = navigation._directions_result(leg(), "ANU", "walking", None, minutes_until_start=45)
    assert "destination_lat" not in result
    assert "destination_lng" not in result


def test_arrived_has_no_coords():
    result = navigation._directions_result(
        leg(distance_m=50, end_location={"latitude": -35.0, "longitude": 149.0}),
        "ANU", "walking", None, minutes_until_start=45,
    )
    assert result["arrived"] is True
    assert "destination_lat" not in result
