package com.example.novav2.service

import android.app.AlarmManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.example.novav2.state.GeofenceRegistrar
import com.example.novav2.state.ReminderRepository
import com.example.novav2.state.ReminderScheduler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * Puts the reminder alarm back whenever the platform may have dropped or moved it: boot, an app
 * update, the clock or time zone changing, and the exact-alarm permission changing.
 *
 * Separate from [BootCompletedReceiver] on purpose - that one returns early when no wearable is
 * paired, and reminders must survive a reboot either way.
 *
 * Time and zone changes (and boot, which may follow either) re-resolve every pending reminder's
 * floating local time first, so "9am" is still 9am after a flight.
 *
 * Boot and an app update also clear every geofence, so [GeofenceRegistrar] is told to re-add
 * them all (the reconcile inside rezoneAll does it).
 */
class ReminderRescheduleReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.applicationContext
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                when (intent.action) {
                    Intent.ACTION_BOOT_COMPLETED,
                    Intent.ACTION_MY_PACKAGE_REPLACED -> {
                        GeofenceRegistrar.markLost(app)
                        ReminderRepository.rezoneAll(app)
                    }
                    Intent.ACTION_TIME_CHANGED,
                    Intent.ACTION_TIMEZONE_CHANGED -> ReminderRepository.rezoneAll(app)
                    AlarmManager.ACTION_SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED ->
                        ReminderScheduler.reconcile(app)
                }
            } finally {
                pending.finish()
            }
        }
    }
}
