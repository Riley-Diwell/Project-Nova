package com.example.novav2.state

import android.content.Context
import com.example.novav2.network.NovaApiClient
import kotlin.math.abs

/**
 * The "couldn't work out when to leave" notice - what an ambient check says when
 * navigation_departure_time ran for a calendar entry but had no travel time to give
 * ([NovaApiClient.DepartureUnknown]: no location, maps unreachable, no route). Without it a leave
 * reminder that can't be worked out just never arrives, which reads exactly like having plenty of
 * time.
 *
 * Pure: the wording, and the once-per-event rule. [DepartureUnknownStore] remembers which event
 * was last told about.
 */
object DepartureUnknownNotice {
    // An ambient check runs every ~10 minutes and each one recomputes the event's start from a
    // whole-minute countdown, so the "same event" test has to tolerate a little drift.
    private const val SAME_EVENT_TOLERANCE_MILLIS = 5 * 60_000L

    /** The event this notice is about - null without a calendar anchor or anything to call it,
     * since "couldn't work out when to leave for (nothing)" isn't worth a notification. */
    data class Event(val subject: String, val startMillis: Long)

    fun eventOf(unknown: NovaApiClient.DepartureUnknown, nowMillis: Long): Event? {
        val subject = unknown.eventTitle ?: unknown.destination ?: return null
        val minutes = unknown.minutesUntilStart ?: return null
        return Event(subject, nowMillis + (minutes * 60_000).toLong())
    }

    /** Whether [event] is one the user hasn't been told about yet. */
    fun isNew(event: Event, lastNotified: Event?): Boolean =
        lastNotified == null || lastNotified.subject != event.subject ||
            abs(lastNotified.startMillis - event.startMillis) > SAME_EVENT_TOLERANCE_MILLIS

    /** Fixed wording, never model-phrased - same stance as AmbientCheckRunner's leave-soon text. */
    fun text(subject: String, reason: String?): String =
        if (reason == "no location from the phone") {
            "Couldn't work out when to leave for $subject - Nova doesn't have your location. " +
                "Check the route yourself."
        } else {
            "Couldn't work out when to leave for $subject. Check the route yourself."
        }
}

/** The last event a [DepartureUnknownNotice] was shown for. Cleared on sign-out
 * ([com.example.novav2.auth.LocalData.wipe]). */
object DepartureUnknownStore {
    private const val PREFS = "nova_departure_unknown"
    private const val KEY_SUBJECT = "subject"
    private const val KEY_START = "start"

    fun lastNotified(context: Context): DepartureUnknownNotice.Event? {
        val prefs = prefs(context)
        val subject = prefs.getString(KEY_SUBJECT, null) ?: return null
        return DepartureUnknownNotice.Event(subject, prefs.getLong(KEY_START, 0L))
    }

    fun save(context: Context, event: DepartureUnknownNotice.Event) {
        prefs(context).edit()
            .putString(KEY_SUBJECT, event.subject)
            .putLong(KEY_START, event.startMillis)
            .apply()
    }

    fun clear(context: Context) {
        prefs(context).edit().clear().apply()
    }

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
