"""
tools/functions/places.py - turning "when I get to the shops" into geofences.

WHAT THIS FILE IS
The place half of set_reminder / update_reminder (reminder_tool.py). A place
reminder is fired by the phone's geofences, so what the phone needs is a list
of circles - {name, lat, lng, radius_m} - and this module is where a spoken
place becomes that list. It runs on the server because the fuzzy matching of a
misheard name, and the Maps key, are both here already (navigation.py).

FOUR WAYS TO NAME A PLACE (the model picks one as `match`)
  here     where the user is now - their location_ctx. "Remind me when I get
           back here."
  one      one specific place - "Woolworths Dickson", "the Chifley library", or
           an address, which is how home, work and a friend's house arrive: the
           model reads the address from the Knowledge Map and passes it as the
           query. Places Text Search, top result.
  chain    any branch of a named business - "when I'm at a Woolworths". Text
           Search, then only the results whose name contains the brand: a bare
           "Woolworths" search also returns the Ampol that sells Woolworths
           fuel, a Coles and a lottery kiosk.
  type     any place of a kind - "when I go to the shops", "next time I'm at a
           petrol station". Nearby Search by Google place type, nearest first,
           from PLACE_TYPES below (a fixed list, so the model can't invent a
           type Google doesn't have).

chain and type are searched around where the user is when they set it
(`near`), out to SEARCH_RADIUS_M. Travel well outside that and those geofences
don't follow - a known limit of resolving once, at set time.

CLUSTERING
Nearby Search is noisy at the scale of a geofence: "shopping_mall" near the
city returns the Canberra Centre, its car park, its loading dock and its
parents' room as four places. Anything within MERGE_M of a cluster's first
member joins that cluster, and the cluster becomes one circle wide enough to
cover its members, named after its best-known member (most Google ratings) -
the centre itself rather than its loading dock. That is also what makes "the shops" mean
shopping districts rather than every shop in one.

FAILURE
Every function here returns (points, error) and never raises. No key, no
network or no match are ordinary - the tool turns the error into a message the
model can re-ask with.
"""
from __future__ import annotations

import math
import os
from typing import Any, Optional

import requests

MAPS_API_KEY = os.environ.get("google_maps_api_key")

MATCHES = ("here", "one", "chain", "type")

# Spoken kind of place -> the Google place types that mean it. The keys are the
# model's enum; the values go to Nearby Search's includedTypes.
PLACE_TYPES: dict[str, tuple[str, ...]] = {
    "shops": ("shopping_mall",),
    "supermarket": ("supermarket", "grocery_store"),
    "petrol_station": ("gas_station",),
    "pharmacy": ("pharmacy", "drugstore"),
    "gym": ("gym", "fitness_center"),
    "library": ("library",),
    "post_office": ("post_office",),
    "cafe": ("cafe", "coffee_shop"),
    "hardware_store": ("hardware_store", "home_improvement_store"),
    "bank": ("bank",),
    "train_station": ("train_station", "light_rail_station"),
    "bus_station": ("bus_station",),
    "park": ("park",),
    "doctor": ("doctor", "medical_clinic"),
}

# Geofences under ~100 m fire unreliably, and a place's own pin is often at one
# edge of it - 150 m is Android's own suggested floor.
MIN_RADIUS_M = 150
MAX_RADIUS_M = 800
# How close two results must be to count as one place (see CLUSTERING).
MERGE_M = 300
# Headroom added around a cluster's farthest member.
CLUSTER_MARGIN_M = 100
SEARCH_RADIUS_M = 20_000
# Per reminder. The phone has 100 geofences in all to share between reminders.
MAX_POINTS = 20

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
NEARBY_SEARCH_URL = "https://places.googleapis.com/v1/places:searchNearby"
FIELD_MASK = (
    "places.displayName,places.formattedAddress,places.location,places.viewport,"
    "places.userRatingCount"
)

Point = dict[str, Any]


def parse_latlng(value: Any) -> Optional[tuple[float, float]]:
    """location_ctx's "lat,lng" as floats, or None."""
    if not isinstance(value, str):
        return None
    try:
        lat_str, lng_str = value.split(",")
        lat, lng = float(lat_str), float(lng_str)
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in metres (haversine)."""
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def resolve(match: str, query: Optional[str], place_type: Optional[str],
            near: Optional[str]) -> tuple[list[Point], Optional[str]]:
    """The geofence circles for a place, or ([], why not)."""
    origin = parse_latlng(near)
    if match == "here":
        if origin is None:
            return [], "I don't know where the user is right now, so 'here' can't be used - ask where they mean."
        return [_point("Here", *origin, MIN_RADIUS_M)], None

    if not MAPS_API_KEY:
        return [], "Places can't be looked up right now (no Maps key configured)."

    if match == "one":
        places = _text_search(query or "", origin, page_size=1)
        if places is None:
            return [], "The place lookup failed - try again in a moment."
        if not places:
            return [], f"Couldn't find {query!r}. Ask the user which one they mean, or for an address."
        return [_place_point(places[0])], None

    if origin is None:
        return [], (
            "I don't know where the user is right now, so there's nowhere to search "
            f"for {'branches' if match == 'chain' else 'places'} around - ask for one specific place."
        )

    if match == "chain":
        places = _text_search(query or "", origin, page_size=20)
        if places is None:
            return [], "The place lookup failed - try again in a moment."
        brand = _normalise(query or "")
        branches = [p for p in places if brand and brand in _normalise(_name(p))]
        if not branches:
            return [], f"Couldn't find any {query!r} near the user. Ask which one they mean."
        return _clusters(branches, origin), None

    types = PLACE_TYPES.get(place_type or "")
    if types is None:
        return [], f"place_type must be one of {', '.join(PLACE_TYPES)}."
    places = _nearby_search(types, origin)
    if places is None:
        return [], "The place lookup failed - try again in a moment."
    if not places:
        return [], "Found no places like that near the user."
    return _clusters(places, origin), None


# --- Places API (New) ---------------------------------------------------------

def _post(url: str, body: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    try:
        r = requests.post(url, json=body, timeout=6, headers={
            "X-Goog-Api-Key": MAPS_API_KEY or "",
            "X-Goog-FieldMask": FIELD_MASK,
        })
        data = r.json()
    except Exception as e:
        print(f"[places] request failed: {e}")
        return None
    if "error" in data:
        print(f"[places] {url.rsplit(':', 1)[-1]} error: {data['error'].get('message')}")
        return None
    return [p for p in data.get("places", []) if _latlng(p) is not None]


def _text_search(query: str, origin: Optional[tuple[float, float]],
                 page_size: int) -> Optional[list[dict[str, Any]]]:
    body: dict[str, Any] = {"textQuery": query, "pageSize": page_size}
    if origin is not None:
        body["locationBias"] = {"circle": {
            "center": {"latitude": origin[0], "longitude": origin[1]},
            "radius": float(SEARCH_RADIUS_M),
        }}
    return _post(TEXT_SEARCH_URL, body)


def _nearby_search(types: tuple[str, ...],
                   origin: tuple[float, float]) -> Optional[list[dict[str, Any]]]:
    return _post(NEARBY_SEARCH_URL, {
        "includedTypes": list(types),
        "maxResultCount": 20,
        "rankPreference": "DISTANCE",
        "locationRestriction": {"circle": {
            "center": {"latitude": origin[0], "longitude": origin[1]},
            "radius": float(SEARCH_RADIUS_M),
        }},
    })


# --- shaping ------------------------------------------------------------------

def _name(place: dict[str, Any]) -> str:
    return (place.get("displayName") or {}).get("text") or place.get("formattedAddress") or "Place"


def _latlng(place: dict[str, Any]) -> Optional[tuple[float, float]]:
    loc = place.get("location") or {}
    lat, lng = loc.get("latitude"), loc.get("longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return None
    return float(lat), float(lng)


def _normalise(text: str) -> str:
    """Lower case, letters and digits only - "Woolworths Metro - Wright" contains
    "woolworths", and "McDonald's" matches "mcdonalds"."""
    return "".join(c for c in text.casefold() if c.isalnum())


def _point(name: str, lat: float, lng: float, radius_m: float) -> Point:
    return {
        "name": name,
        "lat": round(lat, 6),
        "lng": round(lng, 6),
        "radius_m": int(min(MAX_RADIUS_M, max(MIN_RADIUS_M, radius_m))),
    }


def _viewport_sides(place: dict[str, Any]) -> Optional[tuple[float, float]]:
    """A place's viewport as (height, width) in metres, or None if it has none."""
    at = _latlng(place)
    viewport = place.get("viewport") or {}
    low, high = viewport.get("low") or {}, viewport.get("high") or {}
    try:
        height = distance_m((low["latitude"], at[1]), (high["latitude"], at[1]))
        width = distance_m((at[0], low["longitude"]), (at[0], high["longitude"]))
    except (KeyError, TypeError):
        return None
    return height, width


def _place_point(place: dict[str, Any]) -> Point:
    """One place as one circle. A big place - a campus, a hospital - says so in
    its viewport, and gets a circle about as wide as its shorter side."""
    lat, lng = _latlng(place)  # type: ignore[misc]  # _post dropped any without
    sides = _viewport_sides(place)
    return _point(_name(place), lat, lng, min(sides) / 2 if sides else MIN_RADIUS_M)


def _clusters(places: list[dict[str, Any]], origin: tuple[float, float]) -> list[Point]:
    """Nearby results merged into one circle per place (see CLUSTERING), nearest
    first, at most MAX_POINTS."""
    groups: list[list[dict[str, Any]]] = []
    for place in places:
        at = _latlng(place)
        for group in groups:
            if distance_m(_latlng(group[0]), at) <= MERGE_M:  # type: ignore[arg-type]
                group.append(place)
                break
        else:
            groups.append([place])

    points = []
    for group in groups:
        spots = [_latlng(p) for p in group]
        lat = sum(at[0] for at in spots) / len(spots)  # type: ignore[index]
        lng = sum(at[1] for at in spots) / len(spots)  # type: ignore[index]
        reach = max(distance_m((lat, lng), at) for at in spots)  # type: ignore[arg-type]
        radius = reach + CLUSTER_MARGIN_M if len(group) > 1 else MIN_RADIUS_M
        best_known = max(group, key=lambda p: p.get("userRatingCount") or 0)
        points.append(_point(_name(best_known), lat, lng, radius))
    points.sort(key=lambda p: distance_m(origin, (p["lat"], p["lng"])))
    return points[:MAX_POINTS]
