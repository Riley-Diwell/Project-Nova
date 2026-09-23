package com.example.novav2.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.example.novav2.state.AmbientNotifier

/**
 * Fires when a [com.example.novav2.state.DepartureAlarmScheduler] alarm goes off - the precise
 * "leave now" moment itself, independent of [SignalMonitorService]'s own polling cadence.
 * Manifest-registered so the system can wake it even if the app process was killed.
 */
class DepartureAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val destination = intent.getStringExtra(EXTRA_DESTINATION)
        val mode = intent.getStringExtra(EXTRA_MODE)

        AmbientNotifier.ensureChannel(context)
        val text = when {
            destination == null -> "Time to leave"
            mode == "walking" -> "Time to head to $destination"
            else -> "Time to leave for $destination"
        }
        AmbientNotifier.notify(context, text, ledFlash = AmbientNotifier.LedFlash.FAST)
    }

    companion object {
        const val EXTRA_DESTINATION = "destination"
        const val EXTRA_MODE = "mode"
    }
}
