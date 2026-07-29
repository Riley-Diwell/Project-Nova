package com.example.nova.ui.screens

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.example.nova.network.NovaApiClient
import com.example.nova.state.CalendarSignal
import com.example.nova.state.CalendarWriter
import com.example.nova.state.UserStateCollector
import kotlinx.coroutines.launch
import java.io.IOException
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeParseException
import java.util.Locale

private enum class VoiceState { IDLE, LISTENING, THINKING, SPEAKING }

/**
 * The backend's get_calendar_range tool is asked to return UTC ISO 8601 (with a 'Z'), but
 * it's LLM-produced input, not a validated wire contract - fall back to treating a bare/
 * offset-less string as the device's local time rather than crashing the round trip.
 */
private fun parseIsoToEpochMillis(iso: String): Long =
    try {
        Instant.parse(iso).toEpochMilli()
    } catch (e: DateTimeParseException) {
        LocalDateTime.parse(iso).atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()
    }

/**
 * Executes the backend's queued "calendar.create_event" actions (add_calendar_event in
 * intent_surface/loop.py) via CalendarWriter, which inserts into the device's Calendar Provider
 * and syncs onward to whichever account owns that calendar (e.g. Google). Assumes
 * WRITE_CALENDAR is already granted - callers must check CalendarWriter.hasPermission first.
 */
private fun writeCalendarActions(
    context: android.content.Context,
    actions: List<NovaApiClient.CalendarAction>,
): Int {
    var created = 0
    for (action in actions) {
        try {
            val start = parseIsoToEpochMillis(action.startIso)
            val end = parseIsoToEpochMillis(action.endIso)
            val uri = CalendarWriter.createEvent(
                context = context,
                title = action.title,
                startMillis = start,
                endMillis = end,
                description = action.description,
            )
            if (uri != null) created++
        } catch (e: DateTimeParseException) {
            // Skip this one action rather than failing the whole batch - LLM-produced input,
            // not a validated wire contract.
        }
    }
    return created
}

/**
 * DESIGN.md §5.1/§5.3: button -> SpeechRecognizer -> POST /event -> TextToSpeech round trip.
 * The transcript + a UserState snapshot go to the backend; the spoken reply is whatever
 * comes back (echo-stub today, Intent Surface once it exists - see backend/app/main.py).
 */
@Composable
fun VoiceScreen() {
    val context = LocalContext.current
    val mainHandler = remember { Handler(Looper.getMainLooper()) }
    val coroutineScope = rememberCoroutineScope()

    var hasPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
        )
    }
    var voiceState by remember { mutableStateOf(VoiceState.IDLE) }
    var transcript by remember { mutableStateOf("") }
    var statusText by remember { mutableStateOf("Tap the mic and say something.") }
    // Holds a finished turn's calendar.create_event actions while we wait on the
    // WRITE_CALENDAR permission prompt, so they can still be applied once granted.
    var pendingCalendarActions by remember { mutableStateOf<List<NovaApiClient.CalendarAction>>(emptyList()) }

    val calendarPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted && pendingCalendarActions.isNotEmpty()) {
            writeCalendarActions(context, pendingCalendarActions)
        }
        pendingCalendarActions = emptyList()
    }

    val textToSpeech = remember { arrayOfNulls<TextToSpeech>(1) }
    DisposableEffect(Unit) {
        val tts = TextToSpeech(context) { }
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {}
            override fun onDone(utteranceId: String?) {
                mainHandler.post { voiceState = VoiceState.IDLE }
            }

            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) {
                mainHandler.post { voiceState = VoiceState.IDLE }
            }
        })
        textToSpeech[0] = tts
        onDispose {
            tts.stop()
            tts.shutdown()
        }
    }

    val speechRecognizer = remember {
        if (SpeechRecognizer.isRecognitionAvailable(context)) {
            SpeechRecognizer.createSpeechRecognizer(context)
        } else {
            null
        }
    }
    DisposableEffect(Unit) {
        onDispose { speechRecognizer?.destroy() }
    }

    fun speak(text: String) {
        voiceState = VoiceState.SPEAKING
        textToSpeech[0]?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "nova-response")
    }

    fun startListening() {
        if (speechRecognizer == null) {
            statusText = "Speech recognition isn't available on this device."
            return
        }
        transcript = ""
        statusText = "Listening…"
        voiceState = VoiceState.LISTENING

        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault())
        }

        speechRecognizer.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}

            override fun onError(error: Int) {
                voiceState = VoiceState.IDLE
                statusText = "Didn't catch that - tap to try again."
            }

            override fun onResults(results: Bundle?) {
                val text = results
                    ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()
                    .orEmpty()
                transcript = text
                if (text.isNotBlank()) {
                    voiceState = VoiceState.THINKING
                    statusText = "Sending to Nova…"
                    coroutineScope.launch {
                        val userState = UserStateCollector.snapshot(context)
                        try {
                            var result: NovaApiClient.EventResult = NovaApiClient.postVoiceEvent(text, userState)
                            // The Intent Surface can pause on a client-executed tool (e.g.
                            // get_calendar_range) it needs on-device data for; resolve it
                            // locally and hand the result back until we get a final answer.
                            // Capped so a misbehaving backend can't loop forever.
                            var hops = 0
                            while (result is NovaApiClient.EventResult.NeedMore && hops < 3) {
                                val need = result as NovaApiClient.EventResult.NeedMore
                                statusText = "Checking your calendar…"
                                val events = when (need.requestType) {
                                    "get_calendar_range" -> {
                                        val from = parseIsoToEpochMillis(need.fromIso)
                                        val to = parseIsoToEpochMillis(need.toIso)
                                        CalendarSignal.rangeSnapshot(context, from, to).orEmpty()
                                    }
                                    else -> emptyList()
                                }
                                result = NovaApiClient.postContinueEvent(need.sessionId, events)
                                hops++
                            }
                            val finalResult = result as? NovaApiClient.EventResult.Final
                            val calendarActions = finalResult?.actions.orEmpty()
                            if (calendarActions.isNotEmpty()) {
                                if (CalendarWriter.hasPermission(context)) {
                                    writeCalendarActions(context, calendarActions)
                                } else {
                                    pendingCalendarActions = calendarActions
                                    calendarPermissionLauncher.launch(Manifest.permission.WRITE_CALENDAR)
                                }
                            }
                            statusText = "Heard you."
                            speak(finalResult?.speech ?: "Sorry, I couldn't finish that.")
                        } catch (e: IOException) {
                            voiceState = VoiceState.IDLE
                            statusText = "Couldn't reach the Nova backend - tap to try again."
                        } catch (e: DateTimeParseException) {
                            voiceState = VoiceState.IDLE
                            statusText = "Nova sent a date it couldn't understand - tap to try again."
                        }
                    }
                } else {
                    voiceState = VoiceState.IDLE
                    statusText = "Didn't catch that - tap to try again."
                }
            }

            override fun onPartialResults(partialResults: Bundle?) {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })

        speechRecognizer.startListening(intent)
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        hasPermission = granted
        if (granted) {
            startListening()
        } else {
            statusText = "Microphone permission is required for voice input."
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(20.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text(
                    text = "Voice round-trip",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    text = statusText,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                if (transcript.isNotBlank()) {
                    Spacer(Modifier.height(12.dp))
                    Text(
                        text = "\"$transcript\"",
                        style = MaterialTheme.typography.bodyLarge
                    )
                }
                Spacer(Modifier.height(20.dp))
                Button(
                    onClick = {
                        if (!hasPermission) {
                            permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
                        } else {
                            startListening()
                        }
                    },
                    enabled = voiceState == VoiceState.IDLE
                ) {
                    Icon(Icons.Default.Mic, contentDescription = null)
                    Spacer(Modifier.width(8.dp))
                    Text(
                        when (voiceState) {
                            VoiceState.LISTENING -> "Listening…"
                            VoiceState.THINKING -> "Thinking…"
                            VoiceState.SPEAKING -> "Speaking…"
                            VoiceState.IDLE -> "Tap to speak"
                        }
                    )
                }
            }
        }
    }
}
