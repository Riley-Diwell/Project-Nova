package com.example.novav2.network

import com.example.novav2.model.PlaceEvent
import com.example.novav2.model.ReminderPlace
import com.example.novav2.model.ReminderRecurrence
import com.example.novav2.model.ReminderSummary
import org.json.JSONArray
import org.json.JSONObject

/**
 * The reminder half of the /event wire (server tools/functions/reminder_tool.py), kept out of
 * NovaApiClient.kt so it can be unit-tested and so that file doesn't grow further. Parsers are
 * internal for ReminderActionParsingTest.
 */

/** A set_reminder Action. At most one of [dueLocal] / [inMinutes] is set and, without a [place],
 * exactly one - the backend validated that - and [trigger] is the Action's own
 * "requested"/"inferred", which becomes the stored reminder's origin ("Suggested by Nova" for
 * inferred). [place] carries the geofence circles the backend resolved
 * (tools/functions/places.py); with it, a time is a deadline and [afterLocal] a "not before". */
data class ReminderAction(
    val text: String,
    val dueLocal: String?,
    val inMinutes: Int?,
    val priority: String,
    val trigger: String,
    val recurrence: ReminderRecurrence?,
    val place: ReminderPlace? = null,
    val everyTime: Boolean = false,
    val afterLocal: String? = null,
)

/** An update_reminder Action - [action] is complete / snooze / edit / delete. */
data class UpdateReminderAction(
    val id: String,
    val action: String,
    val label: String,
    val text: String?,
    val dueLocal: String?,
    val inMinutes: Int?,
    val shiftMinutes: Int?,
    val recurrence: ReminderRecurrence?,
    val place: ReminderPlace? = null,
    /** Null to leave it as it is. */
    val everyTime: Boolean? = null,
)

/** Whole {tool, input, trigger, ran} entries for [tool] that actually ran - ran=false was
 * refused (or rejected by the tool's own validation) and must not be carried out. The sibling
 * of NovaApiClient's inputsFor, returning the whole entry because reminders need `trigger` too. */
internal fun JSONArray.ranActions(tool: String): List<JSONObject> =
    (0 until length()).mapNotNull { i ->
        val obj = optJSONObject(i) ?: return@mapNotNull null
        if (obj.optString("tool") != tool || !obj.optBoolean("ran", false)) return@mapNotNull null
        obj
    }

internal fun parseReminderActions(actions: JSONArray?): List<ReminderAction> =
    actions?.ranActions("set_reminder").orEmpty().mapNotNull { action ->
        val input = action.optJSONObject("input") ?: return@mapNotNull null
        val text = input.optStringOrNull("text")?.trim().orEmpty()
        val due = input.optStringOrNull("due_local")
        val minutes = input.optIntOrNull("in_minutes")?.takeIf { it > 0 }
        val place = parsePlace(input.optJSONObject("place"))
        if (text.isEmpty() || (due == null && minutes == null && place == null)) return@mapNotNull null
        ReminderAction(
            text = text,
            dueLocal = due,
            inMinutes = if (due == null) minutes else null,
            priority = input.optStringOrNull("priority") ?: "normal",
            trigger = action.optStringOrNull("trigger") ?: "requested",
            recurrence = if (place == null) parseRecurrence(input.optJSONObject("recurrence")) else null,
            place = place,
            everyTime = place != null && input.optBoolean("every_time", false),
            afterLocal = if (place != null) input.optStringOrNull("after_local") else null,
        )
    }

internal fun parseUpdateReminderActions(actions: JSONArray?): List<UpdateReminderAction> =
    actions?.ranActions("update_reminder").orEmpty().mapNotNull { action ->
        val input = action.optJSONObject("input") ?: return@mapNotNull null
        val id = input.optStringOrNull("reminder_id") ?: return@mapNotNull null
        val verb = input.optStringOrNull("action") ?: return@mapNotNull null
        if (verb !in setOf("complete", "snooze", "edit", "delete")) return@mapNotNull null
        UpdateReminderAction(
            id = id,
            action = verb,
            label = input.optStringOrNull("label").orEmpty(),
            text = input.optStringOrNull("text"),
            dueLocal = input.optStringOrNull("due_local"),
            inMinutes = input.optIntOrNull("in_minutes"),
            shiftMinutes = input.optIntOrNull("shift_minutes"),
            recurrence = parseRecurrence(input.optJSONObject("recurrence")),
            place = parsePlace(input.optJSONObject("place")),
            everyTime = if (input.has("every_time") && !input.isNull("every_time")) input.optBoolean("every_time") else null,
        )
    }

/** The backend's resolved place - {on, label, points: [{name, lat, lng, radius_m}]}. Null
 * unless it has an event and at least one usable circle: a place reminder with nowhere to watch
 * would never go off. */
internal fun parsePlace(obj: JSONObject?): ReminderPlace? {
    val on = PlaceEvent.fromWire(obj?.optStringOrNull("on")) ?: return null
    val points = ReminderPlace.decodePoints(obj?.optJSONArray("points"))
    if (points.isEmpty()) return null
    return ReminderPlace(on, obj?.optStringOrNull("label") ?: points.first().name, points)
}

internal fun parseRecurrence(obj: JSONObject?): ReminderRecurrence? {
    val frequency = obj?.optStringOrNull("frequency")?.lowercase() ?: return null
    if (frequency !in ReminderRecurrence.FREQUENCIES) return null
    return ReminderRecurrence(
        frequency = frequency,
        interval = obj.optIntOrNull("interval")?.coerceAtLeast(1) ?: 1,
        count = obj.optIntOrNull("count")?.takeIf { it > 0 },
        untilLocal = obj.optStringOrNull("until"),
    )
}

/** schemas/user_state.py's ReminderInfo, snake_case. */
internal fun ReminderSummary.toJson(): JSONObject = JSONObject().apply {
    put("id", id)
    put("text", text)
    put("due_local", dueLocal ?: JSONObject.NULL)
    put("minutes_until_due", minutesUntilDue ?: JSONObject.NULL)
    put("status", status)
    put("priority", priority)
    put("fired_minutes_ago", firedMinutesAgo ?: JSONObject.NULL)
    put("recurrence", recurrence ?: JSONObject.NULL)
    put("place", place ?: JSONObject.NULL)
    put("every_time", everyTime)
    put("place_after_local", placeAfterLocal ?: JSONObject.NULL)
}

internal fun List<ReminderSummary>.toReminderJsonArray(): JSONArray =
    JSONArray().also { arr -> forEach { arr.put(it.toJson()) } }

private fun JSONObject.optStringOrNull(key: String): String? =
    if (has(key) && !isNull(key)) optString(key).takeIf { it.isNotBlank() } else null

private fun JSONObject.optIntOrNull(key: String): Int? =
    if (has(key) && !isNull(key)) {
        when (val v = opt(key)) {
            is Number -> v.toInt()
            is String -> v.toIntOrNull()
            else -> null
        }
    } else null
