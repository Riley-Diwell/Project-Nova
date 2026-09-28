package com.example.novav2.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.example.novav2.state.ReminderEngine
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * The reminder alarm going off - [com.example.novav2.state.ReminderScheduler] keeps exactly one
 * set, for whatever is due next. Manifest-registered so it wakes a killed process. goAsync()
 * because delivery reads Room.
 */
class ReminderAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                ReminderEngine.onAlarm(context.applicationContext)
            } finally {
                pending.finish()
            }
        }
    }
}
