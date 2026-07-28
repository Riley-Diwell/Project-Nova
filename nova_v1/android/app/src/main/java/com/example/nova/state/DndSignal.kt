package com.example.nova.state

import android.app.NotificationManager
import android.content.Context

/** Cheap signal (DESIGN.md §5.2): current DND/ringer state. No runtime permission needed to read it. */
object DndSignal {
    fun isDndActive(context: Context): Boolean {
        val notificationManager =
            context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        return notificationManager.currentInterruptionFilter != NotificationManager.INTERRUPTION_FILTER_ALL
    }
}
