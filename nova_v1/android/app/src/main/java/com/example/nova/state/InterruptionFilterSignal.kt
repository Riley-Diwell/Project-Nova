package com.example.nova.state

import android.app.NotificationManager
import android.content.Context

/**
 * No-permission signal: granular DND interruption filter level. More informative than the
 * boolean [DndSignal] - "priority" (let contacts through) differs meaningfully from
 * "none" (total silence).
 */
object InterruptionFilterSignal {
    fun currentFilter(context: Context): String {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        return when (nm.currentInterruptionFilter) {
            NotificationManager.INTERRUPTION_FILTER_ALL -> "all"
            NotificationManager.INTERRUPTION_FILTER_PRIORITY -> "priority"
            NotificationManager.INTERRUPTION_FILTER_ALARMS -> "alarms"
            NotificationManager.INTERRUPTION_FILTER_NONE -> "none"
            else -> "unknown"
        }
    }
}
