package com.example.novav2.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.os.Build
import android.os.IBinder
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.example.novav2.R
import com.example.novav2.state.AudioRouteSignal

/**
 * Reads a reminder aloud through connected headphones.
 * Only ever started by [com.example.novav2.state.ReminderEngine] when InterruptionPolicy said to
 * speak, which requires a headset and the user's opt-in; checked again here just before speaking,
 * because a headset can disconnect in between. Never plays through the phone speaker.
 *
 * A short foreground service (shortService on API 34+, dataSync below) because TTS needs a few
 * seconds of a live process. Starting it from the exact-alarm broadcast is one of Android's
 * allowed background-start exemptions.
 */
class ReminderSpeechService : Service() {
    private var tts: TextToSpeech? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val lines = intent?.getStringArrayListExtra(EXTRA_LINES).orEmpty()
        startInForeground()
        if (lines.isEmpty() || !headsetConnected()) {
            stopNow()
            return START_NOT_STICKY
        }
        tts = TextToSpeech(this) { status ->
            val engine = tts
            if (status != TextToSpeech.SUCCESS || engine == null || !headsetConnected()) {
                stopNow()
                return@TextToSpeech
            }
            engine.setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {}
                override fun onDone(utteranceId: String?) {
                    if (utteranceId == LAST_UTTERANCE) stopNow()
                }
                @Deprecated("Deprecated in Java")
                override fun onError(utteranceId: String?) = stopNow()
            })
            lines.forEachIndexed { i, line ->
                val id = if (i == lines.lastIndex) LAST_UTTERANCE else "reminder-$i"
                engine.speak(line, TextToSpeech.QUEUE_ADD, null, id)
            }
        }
        return START_NOT_STICKY
    }

    /** API 34's shortService limit (about 3 minutes) - far longer than a reminder takes. */
    override fun onTimeout(startId: Int) = stopNow()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        tts?.stop()
        tts?.shutdown()
        tts = null
        super.onDestroy()
    }

    private fun headsetConnected(): Boolean =
        AudioRouteSignal.currentAudioRoute(this).let { it.wiredHeadsetConnected || it.bluetoothAudioConnected }

    private fun startInForeground() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            getSystemService(NotificationManager::class.java).createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Reading reminders aloud", NotificationManager.IMPORTANCE_MIN)
            )
        }
        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Nova")
            .setContentText("Reading a reminder")
            .setSmallIcon(R.mipmap.ic_launcher)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .build()
        when {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE ->
                startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SHORT_SERVICE)
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q ->
                startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
            else -> startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun stopNow() {
        @Suppress("DEPRECATION")
        stopForeground(true)
        stopSelf()
    }

    companion object {
        private const val CHANNEL_ID = "nova_reminder_speech"
        // 42-45 are the app's other services, 46-48 ReminderNotifier.
        private const val NOTIFICATION_ID = 49
        private const val EXTRA_LINES = "lines"
        private const val LAST_UTTERANCE = "reminder-last"

        fun speak(context: Context, lines: List<String>) {
            if (lines.isEmpty()) return
            try {
                ContextCompat.startForegroundService(
                    context,
                    Intent(context, ReminderSpeechService::class.java)
                        .putStringArrayListExtra(EXTRA_LINES, ArrayList(lines)),
                )
            } catch (e: IllegalStateException) {
                // Background start refused (not from an alarm after all) - the buzz and the
                // notification have already gone out, so the reminder is not lost.
            }
        }
    }
}
