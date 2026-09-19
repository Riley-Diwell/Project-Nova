package com.example.novav2.state

import android.content.Context

/**
 * The user's declared "how do I usually get to uni" preference (Settings), read by
 * [UserStateCollector] into every [com.example.novav2.model.UserState.preferredTravelMode] -
 * navigation_departure_time's default mode when the model doesn't name one.
 *
 * Declared, not inferred: unlike every other file in this package, this isn't sensed - it's a
 * plain user setting. Backed by SharedPreferences rather than a signal source, so it's readable
 * from SignalMonitorService the same way as any other signal, and survives app restarts (unlike
 * UserProfile's rememberSaveable, which is Activity-scoped and doesn't outlive a process kill).
 */
object TravelModePreference {
    private const val PREFS_NAME = "nova_settings"
    private const val KEY_TRAVEL_MODE = "preferred_travel_mode"

    /** One of "driving" / "transit" / "walking", or null if the user hasn't set one. */
    fun get(context: Context): String? =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(KEY_TRAVEL_MODE, null)

    fun set(context: Context, mode: String?) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_TRAVEL_MODE, mode)
            .apply()
    }
}
