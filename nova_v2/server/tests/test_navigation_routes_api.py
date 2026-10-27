"""navigation_departure_time on the Routes API and Places API (New).

New Google Cloud projects can't enable the legacy Directions or Find Place
APIs, so the tool speaks Routes' computeRoutes and Places' Text Search. These
pin the request shapes Google needs and the parts of the response the tool
reads.
"""
from app.tools.functions import navigation


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def transit_leg():
    # Walk 5 min to the stop, ride 08:40-09:00 UTC, walk 3 min to the door.
    return {
        "distanceMeters": 6000,
        "duration": "1680s",
        "localizedValues": {"duration": {"text": "28 mins"}},
        "steps": [
            {"staticDuration": "300s", "navigationInstruction": {"instructions": "Walk to Gungahlin Pl"}},
            {"staticDuration": "1200s", "transitDetails": {"stopDetails": {
                "departureTime": "2026-10-27T08:40:00Z", "arrivalTime": "2026-10-27T09:00:00.123456789Z"}}},
            {"staticDuration": "180s"},
        ],
    }


def test_transit_times_add_the_walks_either_side():
    # UTC+11 (Canberra in daylight time): 08:35Z is 7:35pm, 09:03Z is 8:03pm.
    assert navigation._transit_times(transit_leg(), 660) == ("7:35pm", "8:03pm")


def test_no_transit_steps_means_no_clock_times():
    assert navigation._transit_times({"steps": [{"staticDuration": "60s"}]}, 0) == (None, None)


def test_transit_result_speaks_the_leave_time():
    result = navigation._directions_result(transit_leg(), "ANU", "transit", "8:05pm", 40, 660)
    assert result["depart_at"] == "7:35pm"
    assert result["spoken"].startswith("Leave 7:35pm — takes 28 mins to ANU, arriving at 8:03pm")
    assert result["leave_in_minutes"] == 12.0


def test_waypoints():
    assert navigation._waypoint("-35.28,149.13") == {
        "location": {"latLng": {"latitude": -35.28, "longitude": 149.13}}}
    assert navigation._waypoint("place_id:abc") == {"placeId": "abc"}
    assert navigation._waypoint("Canberra Centre") == {"address": "Canberra Centre"}


def test_routes_request_shape(monkeypatch):
    monkeypatch.setattr(navigation, "MAPS_API_KEY", "key")
    calls = []

    def post(url, json, timeout, headers):
        calls.append((url, json, headers))
        return FakeResponse({"routes": [{"legs": [{"distanceMeters": 5000, "duration": "600s"}]}]})

    monkeypatch.setattr(navigation.requests, "post", post)
    result = navigation.NavigationTool()._execute(
        {"destination": "ANU", "origin": "-35.28,149.13", "mode": "driving", "arrival_time": "9:00am"})

    url, body, headers = calls[0]
    assert url == navigation.ROUTES_URL
    assert body["travelMode"] == "DRIVE"
    # Routes rejects arrivalTime for anything but transit.
    assert "arrivalTime" not in body
    assert headers["X-Goog-Api-Key"] == "key"
    assert headers["X-Goog-FieldMask"] == navigation.ROUTES_FIELD_MASK
    assert result["success"] is True
    assert result["duration"] == "10 mins"


def test_transit_request_carries_arrival_time(monkeypatch):
    monkeypatch.setattr(navigation, "MAPS_API_KEY", "key")
    bodies = []

    def post(url, json, timeout, headers):
        bodies.append(json)
        return FakeResponse({"routes": [{"legs": [{"distanceMeters": 5000, "duration": "600s"}]}]})

    monkeypatch.setattr(navigation.requests, "post", post)
    navigation.NavigationTool()._execute(
        {"destination": "ANU", "origin": "-35.28,149.13", "mode": "transit",
         "arrival_time": "9:00am", "utc_offset_minutes": 660})
    assert bodies[0]["arrivalTime"].endswith(":00:00Z")
    assert bodies[0]["arrivalTime"][11:13] == "22"


def test_denied_key_is_maps_unavailable_without_a_places_lookup(monkeypatch):
    monkeypatch.setattr(navigation, "MAPS_API_KEY", "key")
    urls = []

    def post(url, json, timeout, headers):
        urls.append(url)
        return FakeResponse({"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "billing"}})

    monkeypatch.setattr(navigation.requests, "post", post)
    result = navigation.NavigationTool()._execute({"destination": "ANU", "origin": "-35.28,149.13"})
    assert result["reason"] == "maps unavailable"
    assert urls == [navigation.ROUTES_URL]


def test_no_route_falls_back_to_places_text_search(monkeypatch):
    monkeypatch.setattr(navigation, "MAPS_API_KEY", "key")
    navigation._RESOLVED_PLACE_CACHE.clear()
    routes_bodies = []

    def post(url, json, timeout, headers):
        if url == navigation.TEXT_SEARCH_URL:
            assert headers["X-Goog-FieldMask"] == "places.id,places.formattedAddress"
            assert "locationBias" in json
            return FakeResponse({"places": [{"id": "pid", "formattedAddress": "Questacon, King Edward Terrace, Parkes ACT 2600"}]})
        routes_bodies.append(json)
        if "placeId" in json["destination"]:
            return FakeResponse({"routes": [{"legs": [{"distanceMeters": 5000, "duration": "600s"}]}]})
        return FakeResponse({})

    monkeypatch.setattr(navigation.requests, "post", post)
    result = navigation.NavigationTool()._execute(
        {"destination": "Questercon", "origin": "-35.28,149.13", "mode": "driving"})
    assert result["success"] is True
    assert result["destination"] == "Questacon, King Edward Terrace, Parkes ACT 2600"
    assert result["spoken"].startswith("(Assuming you meant Questacon, King Edward Terrace, Parkes ACT 2600)")
    assert routes_bodies[-1]["destination"] == {"placeId": "pid"}
    navigation._RESOLVED_PLACE_CACHE.clear()
