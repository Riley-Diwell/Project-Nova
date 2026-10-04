package com.example.novav2.model

import org.json.JSONArray
import org.json.JSONObject

/** Arriving at a place, or leaving it - set_reminder's place.on. */
enum class PlaceEvent(val wire: String) {
    ARRIVE("arrive"),
    LEAVE("leave");

    companion object {
        fun fromWire(value: String?): PlaceEvent? = entries.firstOrNull { it.wire == value }
    }
}

/** One geofence circle, as the server's places.py resolved it. */
data class GeoPoint(
    val name: String,
    val lat: Double,
    val lng: Double,
    val radiusMeters: Float,
)

/**
 * Where a place reminder goes off: on [on] at any of [points] - one circle for "Woolworths
 * Dickson" or home, several for "the shops" or "any Woolworths". [label] is how the user said it
 * ("the shops"), for the notification and the list.
 */
data class ReminderPlace(
    val on: PlaceEvent,
    val label: String,
    val points: List<GeoPoint>,
) {
    /** "arrive: the shops" - ReminderInfo.place on the wire (schemas/user_state.py). */
    fun describe(): String = "${on.wire}: $label"

    companion object {
        /** The server's place.points / ReminderEntity.placePoints, in the server's own field
         * names so the Action's JSON can be stored as-is. Malformed entries are dropped. */
        fun decodePoints(json: String?): List<GeoPoint> {
            if (json.isNullOrBlank()) return emptyList()
            val array = try { JSONArray(json) } catch (e: Exception) { return emptyList() }
            return (0 until array.length()).mapNotNull { i -> array.optJSONObject(i)?.toGeoPoint() }
        }

        fun decodePoints(array: JSONArray?): List<GeoPoint> =
            if (array == null) emptyList()
            else (0 until array.length()).mapNotNull { i -> array.optJSONObject(i)?.toGeoPoint() }

        fun encodePoints(points: List<GeoPoint>): String = JSONArray().also { array ->
            points.forEach {
                array.put(JSONObject()
                    .put("name", it.name)
                    .put("lat", it.lat)
                    .put("lng", it.lng)
                    .put("radius_m", it.radiusMeters.toDouble()))
            }
        }.toString()

        private fun JSONObject.toGeoPoint(): GeoPoint? {
            val lat = optDouble("lat", Double.NaN)
            val lng = optDouble("lng", Double.NaN)
            val radius = optDouble("radius_m", Double.NaN)
            if (lat.isNaN() || lng.isNaN() || radius.isNaN() || radius <= 0) return null
            if (lat !in -90.0..90.0 || lng !in -180.0..180.0) return null
            return GeoPoint(optString("name").ifBlank { "Place" }, lat, lng, radius.toFloat())
        }
    }
}
