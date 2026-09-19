package com.example.novav2.state

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import com.example.novav2.network.NovaApiClient
import com.example.novav2.service.DepartureAlarmReceiver

/**
 * Schedules a precise one-shot alarm for a navigation_departure_time countdown
 * ([NovaApiClient.ScheduledDeparture]), so "you need to leave" fires at the real moment instead
 * of waiting for [com.example.novav2.service.SignalMonitorService]'s next ~10-minute poll.
 *
 * No SCHEDULE_EXACT_ALARM / special "Alarms & reminders" permission needed: setAndAllowWhileIdle
 * fires even during Doze without it, just without a to-the-second guarantee - fine here, since
 * this is already an improvement over the poll cadence, not a promise of precision.
 */
object DepartureAlarmScheduler {
    // Fixed, not per-commitment: a later schedule() call for the same (or a newly imminent)
    // commitment naturally replaces the previous alarm via FLAG_UPDATE_CURRENT rather than
    // stacking a second one - there is only ever one "next thing to leave for" worth alerting on.
    private const val REQUEST_CODE = 100

    // Ignore anything this far out - a countdown this large came from a commitment well outside
    // the horizon that made asking worthwhile in the first place, so scheduling against it would
    // likely be superseded by a fresher check long before it fires anyway.
    private const val MAX_MINUTES_AHEAD = 180.0

    fun schedule(context: Context, departure: NovaApiClient.ScheduledDeparture) {
        if (departure.leaveInMinutes > MAX_MINUTES_AHEAD) return

        // Already due (leaveInMinutes <= 0) fires almost immediately rather than being dropped -
        // "should have left already" is exactly when this alarm still needs to speak up.
        val delayMinutes = departure.leaveInMinutes.coerceAtLeast(0.0)
        val triggerAtMillis = System.currentTimeMillis() + (delayMinutes * 60_000).toLong()

        val intent = Intent(context, DepartureAlarmReceiver::class.java).apply {
            putExtra(DepartureAlarmReceiver.EXTRA_DESTINATION, departure.destination)
            putExtra(DepartureAlarmReceiver.EXTRA_MODE, departure.mode)
        }
        val pendingIntent = PendingIntent.getBroadcast(
            context, REQUEST_CODE, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        context.getSystemService(AlarmManager::class.java)
            .setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAtMillis, pendingIntent)
    }
}
