package com.example.novav2.service

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.ProcessLifecycleOwner
import com.example.novav2.R
import com.example.novav2.data.NovaDatabase
import com.example.novav2.data.toEntity
import com.example.novav2.model.ChatMessage
import com.example.novav2.network.NovaApiClient
import com.example.novav2.state.AmbientNotifier
import com.example.novav2.state.CalendarSignal
import com.example.novav2.state.CalendarWriter
import com.example.novav2.state.DepartureAlarmScheduler
import com.example.novav2.state.UserStateCollector
import com.example.novav2.state.parseIsoToEpochMillis
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import java.io.IOException
import java.net.SocketTimeoutException
import java.time.format.DateTimeParseException
import java.util.Locale

/**
 * The power-button assist gesture's headless path (see AssistTrampolineActivity): listens,
 * round-trips through the same POST /event flow VoiceScreen.kt's sendMessage() uses, and
 * delivers the reply by voice (TextToSpeech) rather than requiring the app to be open. Also
 * posts an AmbientNotifier notification (its doc comment already frames it as "the delivery
 * mechanism for anything Nova says without a live conversation to speak into", which is exactly
 * this) - but only while the app is backgrounded, see finishTurn().
 * Runs as a foreground service (Android requires it for mic use outside an active Activity) with
 * its own low-priority status notification, kept separate from AmbientNotifier's actual reply.
 *
 * Deliberately narrower than sendMessage(): calendar add/edit actions only apply if
 * WRITE_CALENDAR is already granted (there is no Activity here to run a permission prompt
 * through), and delete_calendar_event actions are always skipped - deleting something needs the
 * user's explicit Yes/No, and there is no UI here to ask for it.
 */
class AssistVoiceService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var speechRecognizer: SpeechRecognizer? = null
    private var textToSpeech: TextToSpeech? = null

    override fun onCreate() {
        super.onCreate()
        ensureStatusChannel()
        AmbientNotifier.ensureChannel(this)
        startForeground(NOTIFICATION_ID, buildStatusNotification("Nova is listening…"))
        startListening()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_NOT_STICKY

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        speechRecognizer?.destroy()
        textToSpeech?.stop()
        textToSpeech?.shutdown()
        scope.cancel()
        super.onDestroy()
    }

    private fun startListening() {
        val hasMicPermission = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (!hasMicPermission || !SpeechRecognizer.isRecognitionAvailable(this)) {
            finishTurn("Sorry, I can't listen right now - open Nova to check microphone access.", episodeId = null)
            return
        }

        val recognizer = SpeechRecognizer.createSpeechRecognizer(this)
        speechRecognizer = recognizer
        val recognizerIntent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault())
        }

        recognizer.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}

            override fun onError(error: Int) {
                finishTurn("Didn't catch that - try again from the app.", episodeId = null)
            }

            override fun onResults(results: Bundle?) {
                val text = results
                    ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()
                    .orEmpty()
                if (text.isBlank()) {
                    finishTurn("Didn't catch that - try again from the app.", episodeId = null)
                } else {
                    handleTranscript(text)
                }
            }

            override fun onPartialResults(partialResults: Bundle?) {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })

        recognizer.startListening(recognizerIntent)
    }

    private fun handleTranscript(text: String) {
        val dao = NovaDatabase.getInstance(applicationContext).chatMessageDao()
        scope.launch {
            dao.insert(ChatMessage(text = text, fromUser = true).toEntity(System.currentTimeMillis()))
            try {
                val userState = UserStateCollector.snapshot(applicationContext)
                var result: NovaApiClient.EventResult = NovaApiClient.postVoiceEvent(text, userState)
                var hops = 0
                while (result is NovaApiClient.EventResult.NeedMore && hops < 3) {
                    val need = result as NovaApiClient.EventResult.NeedMore
                    val events = when (need.requestType) {
                        "get_calendar_range" -> {
                            val from = parseIsoToEpochMillis(need.fromIso)
                            val to = parseIsoToEpochMillis(need.toIso)
                            CalendarSignal.rangeSnapshot(applicationContext, from, to).orEmpty()
                        }
                        else -> emptyList()
                    }
                    result = NovaApiClient.postContinueEvent(need.sessionId, events)
                    hops++
                }

                val finalResult = result as? NovaApiClient.EventResult.Final
                if (CalendarWriter.hasPermission(applicationContext)) {
                    finalResult?.actions?.forEach { applyCalendarAdd(it) }
                    finalResult?.editActions?.forEach { applyCalendarEdit(it) }
                }
                finalResult?.scheduledDeparture?.let {
                    DepartureAlarmScheduler.schedule(applicationContext, it)
                }

                val reply = finalResult?.speech?.takeIf { it.isNotBlank() }
                    ?: "Sorry, I couldn't finish that - try again from the app."
                dao.insert(ChatMessage(text = reply, fromUser = false).toEntity(System.currentTimeMillis()))
                finishTurn(reply, finalResult?.episodeId)
            } catch (e: SocketTimeoutException) {
                finishTurn("Sorry, that's taking too long - try again from the app.", episodeId = null)
            } catch (e: IOException) {
                finishTurn("Sorry, I couldn't reach the network - try again from the app.", episodeId = null)
            } catch (e: DateTimeParseException) {
                finishTurn("Sorry, something about that didn't come through right.", episodeId = null)
            }
        }
    }

    private fun applyCalendarAdd(action: NovaApiClient.CalendarAction) {
        try {
            val start = parseIsoToEpochMillis(action.startIso)
            val end = parseIsoToEpochMillis(action.endIso)
            CalendarWriter.createEvent(
                context = applicationContext,
                title = action.title,
                startMillis = start,
                endMillis = end,
                description = action.description,
                location = action.location,
                rrule = action.rrule,
            )
        } catch (e: DateTimeParseException) {
            // Skip this one action rather than failing the whole turn - LLM-produced input, not
            // a validated wire contract (same stance as VoiceScreen.kt's writeCalendarActions).
        }
    }

    private fun applyCalendarEdit(action: NovaApiClient.EditCalendarAction) {
        val start = action.startIso?.let {
            try { parseIsoToEpochMillis(it) } catch (e: DateTimeParseException) { null }
        }
        val end = action.endIso?.let {
            try { parseIsoToEpochMillis(it) } catch (e: DateTimeParseException) { null }
        }
        CalendarWriter.updateEvent(
            context = applicationContext,
            eventId = action.eventId,
            title = action.title,
            startMillis = start,
            endMillis = end,
            description = action.description,
            location = action.location,
            rrule = action.rrule,
        )
    }

    /** Delivers the turn's outcome by voice, then tears the service down once speech finishes.
     * Also posts an AmbientNotifier notification, but only if the app isn't in the foreground -
     * if it is, the reply already lands in VoiceScreen's chat thread on its own (ChatViewModel
     * observes the same DAO this writes to), so a system notification on top would just be a
     * second, redundant announcement of something already on screen. */
    private fun finishTurn(reply: String, episodeId: String?) {
        if (!isAppInForeground()) {
            AmbientNotifier.notify(applicationContext, reply)
        }
        speak(reply, episodeId)
    }

    /** Main-thread only (true of every finishTurn() caller here: SpeechRecognizer callbacks and
     * this service's own Dispatchers.Main scope). */
    private fun isAppInForeground(): Boolean =
        ProcessLifecycleOwner.get().lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)

    private fun speak(text: String, episodeId: String?) {
        val limit = TextToSpeech.getMaxSpeechInputLength()
        val utterance = if (text.length > limit) text.take(limit) else text

        val tts = TextToSpeech(this) { status ->
            if (status == TextToSpeech.SUCCESS) {
                textToSpeech?.speak(utterance, TextToSpeech.QUEUE_FLUSH, null, "nova-assist-response")
            } else {
                stopSelfSafely()
            }
        }
        textToSpeech = tts
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {}

            override fun onDone(utteranceId: String?) {
                // Heard out to the end - same accept signal VoiceScreen's silence-based
                // reportOutcome uses, there being no stop button here to reject with instead.
                if (episodeId != null) {
                    scope.launch { NovaApiClient.postOutcome(episodeId, accepted = true) }
                }
                stopSelfSafely()
            }

            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) {
                stopSelfSafely()
            }
        })
    }

    private fun stopSelfSafely() {
        @Suppress("DEPRECATION")
        stopForeground(true)
        stopSelf()
    }

    private fun ensureStatusChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(
                NotificationChannel(STATUS_CHANNEL_ID, "Nova assist status", NotificationManager.IMPORTANCE_HIGH)
            )
        }
    }

    /** HIGH + PRIORITY_HIGH so the very first post (still LISTENING, see startListening())
     * peeks onto the screen and wakes it if it's asleep - the point being triggered this way is
     * that you often can't see the phone at all, so a quiet shade notification alone isn't
     * enough to know Nova actually heard the gesture. setOnlyAlertOnce keeps the later THINKING/
     * replied updates from re-triggering that peek on every single content change - only the
     * first post (a new notification, not an update) actually needs it. VISIBILITY_PUBLIC shows
     * the text on the lock screen too rather than just "new notification". */
    private fun buildStatusNotification(text: String): Notification =
        NotificationCompat.Builder(this, STATUS_CHANNEL_ID)
            .setContentTitle("Nova")
            .setContentText(text)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setOnlyAlertOnce(true)
            .build()

    companion object {
        // "_v2": IMPORTANCE_HIGH only takes effect on a channel's *first* creation (see
        // AmbientNotifier's own CHANNEL_ID comment) - this channel already shipped once as
        // IMPORTANCE_LOW, so keeping the old id would silently keep every device that already
        // saw it stuck without the heads-up peek this now depends on.
        private const val STATUS_CHANNEL_ID = "assist_voice_status_v2"
        private const val NOTIFICATION_ID = 44
    }
}
