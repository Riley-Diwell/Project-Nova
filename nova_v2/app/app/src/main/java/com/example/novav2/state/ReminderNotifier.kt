package com.example.novav2.state

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.example.novav2.MainActivity
import com.example.novav2.R
import com.example.novav2.data.ReminderEntity
import com.example.novav2.service.ReminderActionReceiver

/**
 * Reminder notifications - their own channel, and one notification per reminder, so a reminder
 * never replaces another (AmbientNotifier posts everything under one id, which is right for a
 * nudge and wrong for this). Each is tagged with the reminder's id under [NOTIFICATION_ID], so
 * tags - not ids - keep them apart and nothing collides with ids 42-45.
 *
 * Done and Snooze go to [ReminderActionReceiver], so they work with the app swiped away.
 *
 * Notification ids: 46 = a reminder (tagged by id), 47 = the group summary for a batch,
 * 48 = the Undo notice after a voice delete (tagged by id).
 */
object ReminderNotifier {
    // IMPORTANCE_HIGH only takes effect on a channel's first creation - bump the suffix to
    // change it (see AmbientNotifier's CHANNEL_ID comment).
    private const val CHANNEL_ID = "nova_reminders_v1"
    private const val NOTIFICATION_ID = 46
    private const val SUMMARY_ID = 47
    private const val UNDO_ID = 48
    private const val GROUP = "com.example.novav2.REMINDERS"
    private const val UNDO_TIMEOUT_MILLIS = 60_000L

    /** Base of the per-reminder action request codes (see ReminderScheduler's registry). */
    private const val REQUEST_CODE_BASE = 3000

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.getSystemService(NotificationManager::class.java).createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Reminders", NotificationManager.IMPORTANCE_HIGH)
            )
        }
    }

    fun canPost(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED &&
            NotificationManagerCompat.from(context).areNotificationsEnabled()

    /** One delivered reminder: [title] is the fixed, code-written line above its text -
     * "Reminder", "Held during COMP2100 Lecture", "Missed at 3:00pm". */
    data class Delivery(val reminder: ReminderEntity, val title: String, val headsUp: Boolean)

    /** Posts [deliveries], grouped under one summary if there is more than one. [groupLabel]
     * names what they were held for, e.g. "3 reminders from COMP2100 Lecture". Returns false if
     * notifications aren't allowed. */
    fun notify(context: Context, deliveries: List<Delivery>, groupLabel: String? = null): Boolean {
        if (deliveries.isEmpty() || !canPost(context)) return false
        ensureChannel(context)
        val manager = NotificationManagerCompat.from(context)
        val grouped = deliveries.size > 1
        try {
            deliveries.forEach { d ->
                val builder = base(context, d.headsUp)
                    .setContentTitle(d.title)
                    .setContentText(d.reminder.text)
                    .setStyle(NotificationCompat.BigTextStyle().bigText(d.reminder.text))
                    .setCategory(NotificationCompat.CATEGORY_REMINDER)
                    .addAction(0, "Done", action(context, d.reminder.id, ReminderActionReceiver.ACTION_DONE, 0))
                    .addAction(0, "Snooze ${ReminderPreferences.snoozeMinutes(context)} min",
                        action(context, d.reminder.id, ReminderActionReceiver.ACTION_SNOOZE, 1))
                if (grouped) builder.setGroup(GROUP)
                manager.notify(d.reminder.id, NOTIFICATION_ID, builder.build())
            }
            if (grouped) {
                val label = groupLabel ?: "${deliveries.size} reminders"
                manager.notify(
                    SUMMARY_ID,
                    base(context, deliveries.any { it.headsUp })
                        .setContentTitle(label)
                        .setStyle(NotificationCompat.InboxStyle().also { style ->
                            deliveries.forEach { style.addLine(it.reminder.text) }
                        })
                        .setGroup(GROUP)
                        .setGroupSummary(true)
                        .build(),
                )
            }
        } catch (e: SecurityException) {
            return false
        }
        return true
    }

    fun cancel(context: Context, id: String) {
        val manager = NotificationManagerCompat.from(context)
        manager.cancel(id, NOTIFICATION_ID)
        manager.cancel(id, UNDO_ID)
        // Android keeps an orphaned summary around; drop it once no reminders are left under it.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val live = context.getSystemService(NotificationManager::class.java).activeNotifications
            if (live.none { it.id == NOTIFICATION_ID }) manager.cancel(SUMMARY_ID)
        }
    }

    /** "Removed 'X'" with Undo, after a voice delete - the phone may be in a pocket, so the
     * undo waits here for a minute rather than behind a dialog. */
    fun notifyUndo(context: Context, reminder: ReminderEntity) {
        if (!canPost(context)) return
        ensureChannel(context)
        try {
            NotificationManagerCompat.from(context).notify(
                reminder.id, UNDO_ID,
                base(context, headsUp = false)
                    .setContentTitle("Removed a reminder")
                    .setContentText(reminder.text)
                    .setTimeoutAfter(UNDO_TIMEOUT_MILLIS)
                    .addAction(0, "Undo", action(context, reminder.id, ReminderActionReceiver.ACTION_UNDO, 2))
                    .build(),
            )
        } catch (e: SecurityException) {
            // Permission revoked between the check and the post - nothing to do.
        }
    }

    private fun base(context: Context, headsUp: Boolean): NotificationCompat.Builder =
        NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_nova)
            .setAutoCancel(true)
            .setContentIntent(openApp(context))
            // Silent means no sound, no vibration and no heads-up peek - it is still in the
            // shade. Used for DND, quiet hours, calls and class (InterruptionPolicy).
            .setSilent(!headsUp)
            .setPriority(if (headsUp) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_LOW)

    private fun openApp(context: Context): PendingIntent =
        PendingIntent.getActivity(
            context, REQUEST_CODE_BASE - 1,
            Intent(context, MainActivity::class.java)
                .setAction(MainActivity.ACTION_OPEN_REMINDERS)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

    /** A distinct request code per reminder and action, so one reminder's Done never carries
     * another's id (PendingIntents with equal codes and intents are the same PendingIntent). */
    private fun action(context: Context, id: String, action: String, slot: Int): PendingIntent =
        PendingIntent.getBroadcast(
            context,
            REQUEST_CODE_BASE + (id.hashCode() and 0xFFFF) * 4 + slot,
            Intent(context, ReminderActionReceiver::class.java)
                .setAction(action)
                .putExtra(ReminderActionReceiver.EXTRA_ID, id),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
}
