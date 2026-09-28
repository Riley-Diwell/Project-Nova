package com.example.novav2.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.example.novav2.state.ReminderNotifier
import com.example.novav2.state.ReminderRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * A reminder notification's Done / Snooze, and the Undo on a voice delete's notice. Goes through
 * the same [ReminderRepository] calls as the Reminders screen, so it works with
 * the app swiped away.
 */
class ReminderActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getStringExtra(EXTRA_ID) ?: return
        val app = context.applicationContext
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                when (intent.action) {
                    ACTION_DONE -> ReminderRepository.complete(app, id)
                    ACTION_SNOOZE -> ReminderRepository.snooze(app, id)
                    ACTION_UNDO -> ReminderRepository.undoCancel(app, id)
                }
                ReminderNotifier.cancel(app, id)
            } finally {
                pending.finish()
            }
        }
    }

    companion object {
        const val ACTION_DONE = "com.example.novav2.action.REMINDER_DONE"
        const val ACTION_SNOOZE = "com.example.novav2.action.REMINDER_SNOOZE"
        const val ACTION_UNDO = "com.example.novav2.action.REMINDER_UNDO"
        const val EXTRA_ID = "reminder_id"
    }
}
