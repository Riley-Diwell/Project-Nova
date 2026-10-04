package com.example.novav2.notes

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
import com.example.novav2.network.NotesApiClient

/**
 * Phone notifications for notes. The device's buzz is the primary feedback;
 * a notification is for what a buzz can't say - *which* note failed and that its text is safe,
 * or that a long capture's summary is ready to read.
 */
object NoteNotifier {
    private const val CHANNEL_ID = "nova_notes"

    // Notification ids: 42 SignalMonitorService, 43 AmbientNotifier, 44 AssistVoiceService,
    // 45 NovaDeviceService, 46-48 ReminderNotifier, 49 ReminderSpeechService.
    private const val ID_PROBLEM = 50
    private const val ID_READY = 51
    private const val EXTRA_NOTE = "com.example.novav2.NOTE_ID"

    /** The note couldn't reach the server; it is kept on the phone and retried. */
    fun queued(context: Context, noteId: String, text: String) = post(
        context, ID_PROBLEM,
        title = "Note kept on your phone",
        body = "Nova will save it as soon as it can reach the server: “${text.take(120)}”",
        noteId = noteId,
    )

    /** A queued note has now been saved - replaces the "kept on your phone" notice. */
    fun savedLater(context: Context, noteId: String, text: String) = post(
        context, ID_PROBLEM,
        title = "Note saved",
        body = "“${text.take(120)}”",
        noteId = noteId,
    )

    /** The server refused it for good. Keeps the text so nothing is lost. */
    fun failed(context: Context, noteId: String, text: String, reason: String) = post(
        context, ID_PROBLEM,
        title = "Note couldn't be saved",
        body = "$reason. What was heard: “${text.take(300)}”",
        noteId = noteId,
    )

    /**
     * Takes down any notification showing this note's words - a deleted note is gone from the
     * shade too. The ids above are shared by every note, so each notification carries its note
     * in [EXTRA_NOTE] and only the one about this note is cancelled. [noteId] null: every note's.
     */
    fun cancelFor(context: Context, noteId: String?) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        manager.activeNotifications
            .filter { it.id == ID_PROBLEM || it.id == ID_READY }
            .filter { noteId == null || it.notification.extras.getString(EXTRA_NOTE) == noteId }
            .forEach { manager.cancel(it.tag, it.id) }
    }

    /** Only worth interrupting for when Nova isn't already on screen. */
    fun summaryReady(context: Context, note: NotesApiClient.Note) {
        val summary = note.summary ?: return
        val detail = buildList {
            if (summary.keyPoints.isNotEmpty()) add("${summary.keyPoints.size} key points")
            if (summary.flaggedMoments.isNotEmpty()) {
                add("${summary.flaggedMoments.size} flagged moment" + if (summary.flaggedMoments.size > 1) "s" else "")
            }
        }.joinToString(", ")
        post(
            context, ID_READY,
            title = "${note.calendarTitle ?: note.displayTitle} notes ready",
            body = detail.ifEmpty { summary.tldr },
            noteId = note.id,
            opensNote = true,
        )
    }

    /** [noteId] tags the notification for [cancelFor]; [opensNote] makes tapping it open that
     * note - only once the server has it. */
    private fun post(
        context: Context, id: Int, title: String, body: String,
        noteId: String? = null, opensNote: Boolean = false,
    ) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) return
        ensureChannel(context)

        val open = Intent(context, MainActivity::class.java)
            .setAction(MainActivity.ACTION_OPEN_NOTE)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            .apply { if (opensNote) noteId?.let { putExtra(MainActivity.EXTRA_NOTE_ID, it) } }
        val pending = PendingIntent.getActivity(
            context, id, open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(pending)
            .setAutoCancel(true)
            .apply { noteId?.let { addExtras(android.os.Bundle().apply { putString(EXTRA_NOTE, it) }) } }
            .build()
        NotificationManagerCompat.from(context).notify(id, notification)
    }

    private fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Notes", NotificationManager.IMPORTANCE_DEFAULT),
        )
    }
}
