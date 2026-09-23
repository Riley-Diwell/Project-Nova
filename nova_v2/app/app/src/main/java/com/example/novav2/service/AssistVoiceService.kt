package com.example.novav2.service

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.example.novav2.R
import com.example.novav2.data.NovaDatabase
import com.example.novav2.data.toEntity
import com.example.novav2.model.ChatMessage
import com.example.novav2.network.NovaApiClient
import com.example.novav2.state.AmbientNotifier
import com.example.novav2.state.AppForegroundState
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
 *
 * Also the delivery path for the Nova device's own BLE voice messages: NovaDeviceService
 * transcribes those on-device (VoskTranscriber) and starts this service with EXTRA_TRANSCRIPT
 * set, skipping straight to handleTranscript() below instead of opening the phone's own mic -
 * from there it is indistinguishable from a transcript the power-button gesture produced, so it
 * gets the same chat-thread write, /event round trip, and spoken reply.
 */
class AssistVoiceService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var speechRecognizer: SpeechRecognizer? = null
    private var textToSpeech: TextToSpeech? = null

    override fun onCreate() {
        super.onCreate()
        ensureStatusChannel()
        AmbientNotifier.ensureChannel(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val transcript = intent?.getStringExtra(EXTRA_TRANSCRIPT)
        if (transcript != null) {
            // Skips the "listening" wording entirely - a BLE transcript arrives already
            // recorded and transcribed (NovaDeviceService), this service never listens for it.
            // Only alerts/peeks if the app isn't already open - if it is, VoiceScreen's chat
            // thread will show this same turn landing shortly (see finishTurn()), so a heads-up
            // "Nova is thinking…" here would just be a redundant interruption over a screen the
            // user is already looking at. Still needs *a* notification either way - Android
            // requires one for a running foreground service - so it drops to PRIORITY_MIN
            // (silent, no status-bar icon) rather than skipping it outright.
            // Started with type dataSync, not microphone: this turn never touches the phone's
            // own mic, and Android 14+ only allows a microphone-type foreground service to
            // *start* while the app is in an eligible foreground state - never true here, since
            // this whole path is triggered by a BLE GATT callback with no visible UI. Requesting
            // dataSync (also declared on this service in the manifest) instead of the mic type
            // sidesteps that check, which otherwise crashes this exact path with a
            // SecurityException every time the hardware button is used while the app is
            // backgrounded.
            startForegroundWithType(
                buildStatusNotification("Nova is thinking…", alerting = !AppForegroundState.isInForeground()),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
            )
            handleTranscript(transcript)
        } else {
            startForegroundWithType(
                buildStatusNotification("Nova is listening…"),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
            )
            startListening()
        }
        return START_NOT_STICKY
    }

    /** Foreground service types didn't exist before API 29 - below that, [type] has nothing to
     * apply to and the plain two-arg overload is the only one available. */
    private fun startForegroundWithType(notification: Notification, type: Int) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, type)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

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
        if (!AppForegroundState.isInForeground()) {
            AmbientNotifier.notify(applicationContext, reply)
        }
        speak(reply, episodeId)
    }

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

    /** HIGH + PRIORITY_HIGH (the [alerting] default) so the very first post (still LISTENING,
     * see startListening()) peeks onto the screen and wakes it if it's asleep - the point being
     * triggered this way is that you often can't see the phone at all, so a quiet shade
     * notification alone isn't enough to know Nova actually heard the gesture. setOnlyAlertOnce
     * keeps later updates from re-triggering that peek on every single content change - only the
     * first post (a new notification, not an update) actually needs it. VISIBILITY_PUBLIC shows
     * the text on the lock screen too rather than just "new notification".
     * [alerting] = false drops to PRIORITY_MIN instead - silent, no status-bar icon, shows only
     * if the shade is pulled down - for callers who already know the phone is right in front of
     * the user (see onStartCommand's BLE-transcript-while-foregrounded case). */
    private fun buildStatusNotification(text: String, alerting: Boolean = true): Notification =
        NotificationCompat.Builder(this, STATUS_CHANNEL_ID)
            .setContentTitle("Nova")
            .setContentText(text)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setOngoing(true)
            .setPriority(if (alerting) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_MIN)
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

        /** String extra: a transcript NovaDeviceService already produced from the Nova
         * device's BLE audio. Its presence is what routes onStartCommand straight to
         * handleTranscript() instead of opening the phone's own mic. */
        const val EXTRA_TRANSCRIPT = "com.example.novav2.extra.TRANSCRIPT"
    }
}
