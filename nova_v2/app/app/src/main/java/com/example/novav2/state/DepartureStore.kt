package com.example.novav2.state

import android.content.Context
import com.example.novav2.network.NovaApiClient

/**
 * The latest leave-by the server computed ([NovaApiClient.ScheduledDeparture]), kept so the
 * home-screen widget can show "Leave by 4:05 for COMP2100". [DepartureAlarmScheduler] only sets an
 * alarm and [DeviceLayers] only keeps the LED cue in memory, so without this the leave-by is gone
 * the moment the process is.
 *
 * Pure: the departure's relative countdowns turned into instants at the moment it arrived, and the
 * rule for when it stops being worth showing. [DepartureStore] does the SharedPreferences part.
 */
data class SavedDeparture(
    val leaveByMillis: Long,
    /** The calendar entry it's for - null for a destination with no calendar anchor, which then
     * only goes stale by [STALE_AFTER_MILLIS]. */
    val eventStartMillis: Long?,
    /** That entry's own title, verbatim (never model-phrased) - also how the widget matches the
     * leave-by to its calendar row, since the server sends no event id. */
    val eventTitle: String?,
    val destination: String?,
) {
    /** Still worth showing at [nowMillis]: the event hasn't started, and the leave-by isn't more
     * than [STALE_AFTER_MILLIS] behind - nothing tells the phone the user actually left. */
    fun isLive(nowMillis: Long): Boolean =
        nowMillis < leaveByMillis + STALE_AFTER_MILLIS &&
            (eventStartMillis == null || nowMillis < eventStartMillis)

    /** When [isLive] next turns false - one of the widget's refresh boundaries. */
    fun expiresAtMillis(): Long =
        minOf(leaveByMillis + STALE_AFTER_MILLIS, eventStartMillis ?: Long.MAX_VALUE)

    companion object {
        const val STALE_AFTER_MILLIS = 15 * 60_000L

        fun from(departure: NovaApiClient.ScheduledDeparture, nowMillis: Long): SavedDeparture =
            SavedDeparture(
                leaveByMillis = nowMillis + (departure.leaveInMinutes.coerceAtLeast(0.0) * 60_000).toLong(),
                eventStartMillis = departure.minutesUntilStart?.let { nowMillis + (it * 60_000).toLong() },
                eventTitle = departure.eventTitle,
                destination = departure.destination,
            )
    }
}

/**
 * Persists the one [SavedDeparture] there is - like [DepartureAlarmScheduler]'s single alarm, a
 * newer departure simply replaces the older one. Written by [DepartureAlarmScheduler.schedule];
 * cleared when the user acknowledges it on the device ([DeviceInteraction]) and on sign-out
 * ([com.example.novav2.auth.LocalData.wipe]). An event starting needs no clearing:
 * [SavedDeparture.isLive] already says no.
 */
object DepartureStore {
    private const val PREFS = "nova_departure"
    private const val KEY_LEAVE_BY = "leave_by"
    private const val KEY_EVENT_START = "event_start"
    private const val KEY_EVENT_TITLE = "event_title"
    private const val KEY_DESTINATION = "destination"

    fun save(context: Context, departure: SavedDeparture) {
        val editor = prefs(context).edit()
            .putLong(KEY_LEAVE_BY, departure.leaveByMillis)
            .putString(KEY_EVENT_TITLE, departure.eventTitle)
            .putString(KEY_DESTINATION, departure.destination)
        val start = departure.eventStartMillis
        if (start != null) editor.putLong(KEY_EVENT_START, start) else editor.remove(KEY_EVENT_START)
        editor.apply()
    }

    fun load(context: Context): SavedDeparture? {
        val prefs = prefs(context)
        if (!prefs.contains(KEY_LEAVE_BY)) return null
        return SavedDeparture(
            leaveByMillis = prefs.getLong(KEY_LEAVE_BY, 0L),
            eventStartMillis = if (prefs.contains(KEY_EVENT_START)) prefs.getLong(KEY_EVENT_START, 0L) else null,
            eventTitle = prefs.getString(KEY_EVENT_TITLE, null),
            destination = prefs.getString(KEY_DESTINATION, null),
        )
    }

    fun clear(context: Context) {
        prefs(context).edit().clear().apply()
    }

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
