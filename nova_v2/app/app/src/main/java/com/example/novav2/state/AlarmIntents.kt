package com.example.novav2.state

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.provider.AlarmClock

/**
 * Fires the backend's queued set_timer/set_alarm actions (alarm_tool.py) by handing off to
 * whatever Clock app is installed, via android.provider.AlarmClock's implicit intents - the same
 * "let the real app do the real work" shape NavigationTool hands routing to Google Maps for.
 *
 * EXTRA_SKIP_UI is set on both so the timer/alarm is applied the moment the Action arrives rather
 * than opening the Clock app for the user to confirm - the same fire-and-forget UX
 * CalendarWriter gives add_calendar_event. Skipping the UI this way requires
 * com.android.alarm.permission.SET_ALARM (normal protection level, declared in
 * AndroidManifest.xml - no runtime prompt); without it the OS silently shows the confirmation
 * screen anyway rather than failing.
 */
object AlarmIntents {

    fun setTimer(context: Context, durationSeconds: Int, label: String?): Boolean {
        val intent = Intent(AlarmClock.ACTION_SET_TIMER).apply {
            putExtra(AlarmClock.EXTRA_LENGTH, durationSeconds)
            putExtra(AlarmClock.EXTRA_SKIP_UI, true)
            if (!label.isNullOrBlank()) putExtra(AlarmClock.EXTRA_MESSAGE, label)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        return launch(context, intent)
    }

    fun setAlarm(context: Context, hour: Int, minute: Int, label: String?): Boolean {
        val intent = Intent(AlarmClock.ACTION_SET_ALARM).apply {
            putExtra(AlarmClock.EXTRA_HOUR, hour)
            putExtra(AlarmClock.EXTRA_MINUTES, minute)
            putExtra(AlarmClock.EXTRA_SKIP_UI, true)
            if (!label.isNullOrBlank()) putExtra(AlarmClock.EXTRA_MESSAGE, label)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        return launch(context, intent)
    }

    /** True on success. False (rather than a crash) when no app on the device handles the
     * intent - a real possibility on an emulator with no Clock app installed. */
    private fun launch(context: Context, intent: Intent): Boolean =
        try {
            context.startActivity(intent)
            true
        } catch (e: ActivityNotFoundException) {
            false
        }
}
