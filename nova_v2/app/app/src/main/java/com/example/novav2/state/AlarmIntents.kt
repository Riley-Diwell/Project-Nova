package com.example.novav2.state

import android.companion.CompanionDeviceManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.os.Build
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

    /**
     * Whether a startActivity from here would actually reach the Clock app. Since Android 10 a
     * background app's activity starts are blocked - silently, with no exception - unless it is
     * exempt; the exemption Nova relies on is a CompanionDeviceManager association with the
     * wearable (NovaDevicePairing). A wearable turn with no association and no Nova screen up
     * would otherwise report a timer that never started.
     */
    fun canOpenClockNow(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return true
        if (AppForegroundState.isInForeground()) return true
        val manager = context.getSystemService(CompanionDeviceManager::class.java) ?: return false
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) manager.myAssociations.isNotEmpty()
            else @Suppress("DEPRECATION") manager.associations.isNotEmpty()
        } catch (e: Exception) {
            false
        }
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
