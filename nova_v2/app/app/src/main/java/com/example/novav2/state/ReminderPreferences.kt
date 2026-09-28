package com.example.novav2.state

import android.content.Context

/**
 * The user's reminder settings (Settings screen). Same SharedPreferences file as
 * [TravelModePreference], so they're readable from a BroadcastReceiver with no UI running.
 */
object ReminderPreferences {
    private const val PREFS_NAME = "nova_settings"
    private const val KEY_SNOOZE_MINUTES = "reminder_snooze_minutes"
    private const val KEY_SPEAK_WITH_HEADPHONES = "reminder_speak_with_headphones"

    const val DEFAULT_SNOOZE_MINUTES = 10
    val SNOOZE_CHOICES = listOf(5, 10, 15, 30)

    /** What the notification's Snooze and a bare voice "snooze it" use. */
    fun snoozeMinutes(context: Context): Int =
        prefs(context).getInt(KEY_SNOOZE_MINUTES, DEFAULT_SNOOZE_MINUTES)

    fun setSnoozeMinutes(context: Context, minutes: Int) {
        prefs(context).edit().putInt(KEY_SNOOZE_MINUTES, minutes).apply()
    }

    /** Read reminders aloud when a headset is connected. Never through the phone speaker - the
     * wearable has no speaker, and discretion is the product. Off until the user opts in. */
    fun speakWithHeadphones(context: Context): Boolean =
        prefs(context).getBoolean(KEY_SPEAK_WITH_HEADPHONES, false)

    fun setSpeakWithHeadphones(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_SPEAK_WITH_HEADPHONES, enabled).apply()
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
