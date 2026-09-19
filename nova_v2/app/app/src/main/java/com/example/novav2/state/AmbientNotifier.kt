package com.example.novav2.state

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.example.novav2.R

/**
 * Posts a local "Nova nudges" notification - the delivery mechanism for anything Nova says
 * without a live conversation to speak into: an ambient heads-up from
 * [com.example.novav2.service.SignalMonitorService], or a precise leave-now alert from
 * [com.example.novav2.service.DepartureAlarmReceiver]. Both need the exact same channel and
 * permission check, so it lives here rather than duplicated in each caller.
 */
object AmbientNotifier {
    // "_v2": IMPORTANCE_HIGH only takes effect on a channel's *first* creation - Android locks
    // importance against later reprogramming (the user can still raise/lower it themselves in
    // system settings, but createNotificationChannel() silently no-ops on an existing id). A
    // device that already has the old "nova_ambient" channel from DEFAULT-importance code would
    // otherwise keep posting quiet, no-heads-up notifications forever despite this change.
    private const val CHANNEL_ID = "nova_ambient_v2"
    private const val NOTIFICATION_ID = 43

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = context.getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    "Nova nudges",
                    // HIGH, not DEFAULT: a leave-now nudge has a real deadline behind it, so it
                    // needs the heads-up banner + sound, not a silent add to the shade.
                    NotificationManager.IMPORTANCE_HIGH,
                )
            )
        }
    }

    /** Returns whether a notification was actually posted - false (never an exception) if [text]
     * is blank or POST_NOTIFICATIONS isn't granted. Callers need this: "the backend had
     * something to say" and "the device actually showed it" are different facts, and
     * AmbientCheckRunner's SPOKE/QUIET/BLOCKED outcome depends on telling them apart rather than
     * reporting success just because delivery was attempted. */
    fun notify(context: Context, text: String): Boolean {
        if (text.isBlank()) return false
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            return false
        }

        NotificationManagerCompat.from(context).notify(
            NOTIFICATION_ID,
            NotificationCompat.Builder(context, CHANNEL_ID)
                .setContentTitle("Nova")
                .setContentText(text)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setAutoCancel(true)
                .build()
        )
        return true
    }
}
