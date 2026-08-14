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
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isShiftPressed
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.nova.model.ChatMessage
import com.example.nova.network.NovaApiClient
import com.example.nova.state.CalendarSignal
import com.example.nova.state.CalendarWriter
import com.example.nova.state.UserStateCollector
import com.example.nova.viewmodel.ChatViewModel
import kotlinx.coroutines.launch
import java.io.IOException
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeParseException
import java.util.Locale

private enum class VoiceState { IDLE, LISTENING, THINKING, SPEAKING }

private val NovaMicPurple = Color(0xFF7C4DFF)

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
                rrule = action.rrule,
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
 * Executes the backend's queued edit_calendar_event actions via CalendarWriter.updateEvent, the
 * same fire-and-forget way writeCalendarActions applies an add - no confirmation, since an edit
 * is easily undone. startIso/endIso are only present when the model actually changed them, so a
 * parse failure there is treated the same as "not provided" rather than dropping the whole edit -
 * unlike a bad add, a partially-applied edit (e.g. new title, unchanged time) is still useful.
 */
private fun writeEditActions(
    context: android.content.Context,
    actions: List<NovaApiClient.EditCalendarAction>,
) {
    for (action in actions) {
        val start = action.startIso?.let {
            try { parseIsoToEpochMillis(it) } catch (e: DateTimeParseException) { null }
        }
        val end = action.endIso?.let {
            try { parseIsoToEpochMillis(it) } catch (e: DateTimeParseException) { null }
        }
        CalendarWriter.updateEvent(
            context = context,
            eventId = action.eventId,
            title = action.title,
            startMillis = start,
            endMillis = end,
            description = action.description,
            rrule = action.rrule,
        )
    }
}

@Composable
private fun MessageBubble(message: ChatMessage) {
    Box(
        modifier = Modifier.fillMaxWidth(),
        contentAlignment = if (message.fromUser) Alignment.CenterEnd else Alignment.CenterStart,
    ) {
        Surface(
            color = if (message.fromUser) {
                MaterialTheme.colorScheme.primary
            } else {
                MaterialTheme.colorScheme.surfaceVariant
            },
            shape = RoundedCornerShape(
                topStart = 16.dp,
                topEnd = 16.dp,
                bottomStart = if (message.fromUser) 16.dp else 4.dp,
                bottomEnd = if (message.fromUser) 4.dp else 16.dp,
            ),
            modifier = Modifier.widthIn(max = 280.dp),
        ) {
            Text(
                text = message.text,
                style = MaterialTheme.typography.bodyLarge,
                color = if (message.fromUser) {
                    MaterialTheme.colorScheme.onPrimary
                } else {
                    MaterialTheme.colorScheme.onSurfaceVariant
                },
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
            )
        }
    }
}

/**
 * DESIGN.md §5.1/§5.3: text or SpeechRecognizer input -> POST /event -> TextToSpeech, rendered
 * as a message thread (user bubbles on the right, Nova's replies on the left) rather than a
 * single last-turn readout, so the reply is always visible even before/without TTS finishing.
 */
@Composable
fun VoiceScreen(bottomBarHeight: Dp = 0.dp) {
    val context = LocalContext.current
    val mainHandler = remember { Handler(Looper.getMainLooper()) }
    val coroutineScope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    val focusManager = LocalFocusManager.current
    val keyboardController = LocalSoftwareKeyboardController.current
    val density = LocalDensity.current

    var hasPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
        )
    }
    var voiceState by remember { mutableStateOf(VoiceState.IDLE) }
    var inputText by remember { mutableStateOf("") }
    var statusText by remember { mutableStateOf("") }
    val chatViewModel: ChatViewModel = viewModel()
    val messages = chatViewModel.messages
    // Holds a finished turn's calendar.create_event actions while we wait on the
    // WRITE_CALENDAR permission prompt, so they can still be applied once granted.
    var pendingCalendarActions by remember { mutableStateOf<List<NovaApiClient.CalendarAction>>(emptyList()) }
    // Same, for edit_calendar_event actions - kept as a separate list from the adds above so
    // each can be routed to the right CalendarWriter call once permission is granted.
    var pendingEditActions by remember { mutableStateOf<List<NovaApiClient.EditCalendarAction>>(emptyList()) }
    // Mirrors the last reply's EventResult.Final.confirmation - "yes_no" shows the quick-reply
    // buttons below; cleared as soon as any new turn is sent (button tap, typed, or spoken),
    // same as the backend's own _PENDING_CONFIRMATION is popped on the next voice turn.
    var pendingConfirmation by remember { mutableStateOf<String?>(null) }
    // delete_calendar_event Actions waiting on the user's explicit Yes/No - see the AlertDialog
    // below. This is the hard gate: unlike pendingCalendarActions (which only waits on a
    // permission prompt and then writes unconditionally), nothing here is ever deleted without
    // this confirmation, regardless of gain or what the model said in speech.
    var pendingDeleteConfirmations by remember { mutableStateOf<List<NovaApiClient.DeleteCalendarAction>>(emptyList()) }
    // Set only while waiting on WRITE_CALENDAR after the user has ALREADY said yes to a specific
    // deletion - holds just that one id so the launcher's callback has something to act on.
    var deleteAwaitingPermission by remember { mutableStateOf<Long?>(null) }

    LaunchedEffect(messages.size, voiceState) {
        if (messages.isNotEmpty()) {
            listState.animateScrollToItem(messages.size - 1)
        }
    }

    val calendarPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            if (pendingCalendarActions.isNotEmpty()) writeCalendarActions(context, pendingCalendarActions)
            if (pendingEditActions.isNotEmpty()) writeEditActions(context, pendingEditActions)
        }
        pendingCalendarActions = emptyList()
        pendingEditActions = emptyList()
    }

    val deletePermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        val eventId = deleteAwaitingPermission
        deleteAwaitingPermission = null
        if (granted && eventId != null) {
            coroutineScope.launch { CalendarWriter.deleteEvent(context, eventId) }
        }
    }

    // The Episode of the reply currently being spoken, held until its Outcome is
    // reported. Atomic and single-shot (getAndSet(null)): onDone and the stop
    // button race whenever the user cuts NOVA off near the end of an utterance,
    // and the turn must be scored once, as whichever got there first.
    val speakingEpisode = remember { java.util.concurrent.atomic.AtomicReference<String?>(null) }
    val outcomeScope = rememberCoroutineScope()
    fun reportOutcome(accepted: Boolean) {
        val episodeId = speakingEpisode.getAndSet(null) ?: return
        outcomeScope.launch { NovaApiClient.postOutcome(episodeId, accepted) }
    }

    val textToSpeech = remember { arrayOfNulls<TextToSpeech>(1) }
    // TextToSpeech initialises asynchronously, and speak() before that finishes
    // is dropped silently - it returns ERROR and says nothing. Track readiness,
    // and hold the one utterance that arrived too early so it can be spoken on
    // init instead of lost.
    val ttsReady = remember { java.util.concurrent.atomic.AtomicBoolean(false) }
    val pendingUtterance = remember { arrayOfNulls<String>(1) }
    DisposableEffect(Unit) {
        val tts = TextToSpeech(context) { status ->
            if (status == TextToSpeech.SUCCESS) {
                ttsReady.set(true)
                pendingUtterance[0]?.let { queued ->
                    pendingUtterance[0] = null
                    mainHandler.post {
                        textToSpeech[0]?.speak(
                            queued, TextToSpeech.QUEUE_FLUSH, null, "nova-response",
                        )
                    }
                }
            } else {
                mainHandler.post {
                    statusText = "Text-to-speech didn't start on this device."
                    voiceState = VoiceState.IDLE
                }
            }
        }
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {}
            override fun onDone(utteranceId: String?) {
                // Heard out to the end. Silence is the accept signal - the user
                // had a stop button and did not reach for it.
                reportOutcome(accepted = true)
                mainHandler.post { voiceState = VoiceState.IDLE }
            }

            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) {
                // The engine failed, which says nothing about whether the user
                // wanted this. Drop the turn rather than scoring it either way.
                speakingEpisode.set(null)
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

    /**
     * Cuts NOVA off mid-sentence. The barge-in that DESIGN.md §5.7 reads as the user's
     * rejection of the turn - and, first and foremost, the control any talking assistant owes
     * its user. It is feedback precisely because it is not a feedback button.
     */
    fun stopSpeaking() {
        textToSpeech[0]?.stop()
        reportOutcome(accepted = false)
        voiceState = VoiceState.IDLE
        statusText = "Stopped."
    }

    fun speak(text: String, episodeId: String?) {
        speakingEpisode.set(episodeId)
        if (text.isBlank()) {
            // The backend returns an empty string when the Intent Surface ends
            // without a final answer. Saying nothing looks identical to a crash
            // from the user's side, so say so instead.
            statusText = "Nova didn't have an answer for that - tap to try again."
            voiceState = VoiceState.IDLE
            // Nothing was said, so there is nothing for the user to accept or
            // reject. Scoring this would blame the tools for an empty reply.
            speakingEpisode.set(null)
            return
        }

        // Anything past the engine's limit is rejected outright, not truncated -
        // and a long recall ("what have I asked you to remember?") is exactly
        // the kind of answer that gets near it.
        val limit = TextToSpeech.getMaxSpeechInputLength()
        val utterance = if (text.length > limit) text.take(limit) else text

        voiceState = VoiceState.SPEAKING
        if (!ttsReady.get()) {
            pendingUtterance[0] = utterance
            return
        }

        val result = textToSpeech[0]
            ?.speak(utterance, TextToSpeech.QUEUE_FLUSH, null, "nova-response")
        if (result != TextToSpeech.SUCCESS) {
            statusText = "Couldn't speak that - tap to try again."
            voiceState = VoiceState.IDLE
            speakingEpisode.set(null)
        }
    }

    /** Sends one turn to the backend, whether it came from typing or from a voice transcript. */
    fun sendMessage(text: String) {
        if (text.isBlank()) return
        chatViewModel.addMessage(text, fromUser = true)
        pendingConfirmation = null
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
                val editActions = finalResult?.editActions.orEmpty()
                if (calendarActions.isNotEmpty() || editActions.isNotEmpty()) {
                    if (CalendarWriter.hasPermission(context)) {
                        if (calendarActions.isNotEmpty()) writeCalendarActions(context, calendarActions)
                        if (editActions.isNotEmpty()) writeEditActions(context, editActions)
                    } else {
                        // One permission request covers both - the launcher's callback flushes
                        // whichever of these two lists actually has something queued.
                        pendingCalendarActions = calendarActions
                        pendingEditActions = editActions
                        calendarPermissionLauncher.launch(Manifest.permission.WRITE_CALENDAR)
                    }
                }
                val deleteActions = finalResult?.deleteActions.orEmpty()
                if (deleteActions.isNotEmpty()) {
                    // Appended, not replaced - a dialog already awaiting an earlier turn's answer
                    // must not be dropped by a new one arriving.
                    pendingDeleteConfirmations = pendingDeleteConfirmations + deleteActions
                }
                val reply = finalResult?.speech ?: "Sorry, I couldn't finish that."
                statusText = ""
                chatViewModel.addMessage(reply, fromUser = false)
                pendingConfirmation = finalResult?.confirmation
                speak(reply, finalResult?.episodeId)
            } catch (e: java.net.SocketTimeoutException) {
                // Distinct from "couldn't reach": the backend IS answering, it
                // just took longer than readTimeout. Worth its own message,
                // because the fix is a slower client rather than a broken server.
                voiceState = VoiceState.IDLE
                statusText = "Nova took too long to answer - tap to try again."
            } catch (e: IOException) {
                voiceState = VoiceState.IDLE
                statusText = "Couldn't reach the Nova backend - tap to try again."
            } catch (e: DateTimeParseException) {
                voiceState = VoiceState.IDLE
                statusText = "Nova sent a date it couldn't understand - tap to try again."
            }
        }
    }

    /** Shared by the send button, the IME "send" action, and a physical Enter key press. */
    fun handleSend() {
        val text = inputText
        if (text.isNotBlank()) {
            inputText = ""
            sendMessage(text)
        }
    }

    fun startListening() {
        if (speechRecognizer == null) {
            statusText = "Speech recognition isn't available on this device."
            return
        }
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
                if (text.isNotBlank()) {
                    sendMessage(text)
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

    fun onMicClick() {
        if (!hasPermission) {
            permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        } else if (voiceState == VoiceState.LISTENING) {
            // Force-finish the utterance now instead of waiting for the
            // recognizer's own silence timeout.
            statusText = "Sending to Nova…"
            speechRecognizer?.stopListening()
        } else if (voiceState == VoiceState.IDLE) {
            startListening()
        }
    }

    // The hard gate for delete_calendar_event: shown for every queued deletion, one at a time,
    // regardless of gain or how the model phrased its speech. Nothing in CalendarWriter runs
    // until the user taps Delete here.
    pendingDeleteConfirmations.firstOrNull()?.let { action ->
        AlertDialog(
            onDismissRequest = { pendingDeleteConfirmations = pendingDeleteConfirmations.drop(1) },
            title = { Text("Delete this event?") },
            text = { Text("\"${action.title}\" will be removed from your calendar.") },
            confirmButton = {
                TextButton(onClick = {
                    pendingDeleteConfirmations = pendingDeleteConfirmations.drop(1)
                    if (CalendarWriter.hasPermission(context)) {
                        coroutineScope.launch { CalendarWriter.deleteEvent(context, action.eventId) }
                    } else {
                        deleteAwaitingPermission = action.eventId
                        deletePermissionLauncher.launch(Manifest.permission.WRITE_CALENDAR)
                    }
                }) { Text("Delete") }
            },
            dismissButton = {
                TextButton(onClick = { pendingDeleteConfirmations = pendingDeleteConfirmations.drop(1) }) {
                    Text("Cancel")
                }
            },
        )
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .pointerInput(Unit) {
                detectTapGestures(onTap = {
                    keyboardController?.hide()
                    focusManager.clearFocus()
                })
            },
    ) {
        if (messages.isEmpty()) {
            Box(modifier = Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
                Text(
                    text = "Say something or type a message to get started.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        } else {
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp),
                horizontalArrangement = Arrangement.End,
            ) {
                TextButton(onClick = { chatViewModel.clearMessages() }) {
                    Text("Clear chat")
                }
            }
            LazyColumn(
                state = listState,
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
                contentPadding = PaddingValues(vertical = 16.dp),
            ) {
                items(messages, key = { it.id }) { message ->
                    MessageBubble(message)
                }
                if (voiceState == VoiceState.THINKING) {
                    item(key = "thinking-indicator") {
                        Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterStart) {
                            Surface(
                                color = MaterialTheme.colorScheme.surfaceVariant,
                                shape = RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp, bottomEnd = 16.dp, bottomStart = 4.dp),
                            ) {
                                Text(
                                    text = "Nova is thinking…",
                                    style = MaterialTheme.typography.bodyMedium.copy(fontStyle = FontStyle.Italic),
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                                )
                            }
                        }
                    }
                }
            }
        }

        if (statusText.isNotBlank()) {
            Text(
                text = statusText,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
            )
        }

        // Only while NOVA is talking, because that is the only moment it means
        // anything. Cutting it off is the user's stop control and, per
        // DESIGN.md §5.7, the turn's rejection - the one negative signal V1
        // collects, and one that costs no extra interaction.
        if (voiceState == VoiceState.SPEAKING) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.Start,
            ) {
                OutlinedButton(onClick = { stopSpeaking() }) {
                    Icon(Icons.Default.Stop, contentDescription = null)
                    Spacer(Modifier.width(8.dp))
                    Text("Stop")
                }
            }
        }

        // Quick replies for a dangling yes/no question (EventOut.confirmation) - voice and
        // typed "Other" answers still go through sendMessage/onMicClick exactly as before,
        // these buttons are just a shortcut into the same path.
        if (pendingConfirmation == "yes_no" && voiceState == VoiceState.IDLE) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedButton(onClick = { sendMessage("Yes") }) { Text("Yes") }
                OutlinedButton(onClick = { sendMessage("No") }) { Text("No") }
            }
        }

        // ime's bottom inset is measured from the true screen edge, which is below the app's
        // own bottom NavigationBar - but that bar doesn't move or shrink when the keyboard
        // opens (NavHost is already offset above it via Scaffold's innerPadding), it just gets
        // covered by the keyboard overlay. So the raw ime value bakes in that bar's height on
        // top of the keyboard's own height; only the portion of ime beyond bottomBarHeight is
        // actually eating into this content's own area and needs to be padded for here.
        val imeBottomPx = WindowInsets.ime.getBottom(density)
        val reservedBottomPx = with(density) { bottomBarHeight.roundToPx() }
        val extraKeyboardPadding = with(density) { (imeBottomPx - reservedBottomPx).coerceAtLeast(0).toDp() }

        HorizontalDivider()
        Surface(tonalElevation = 3.dp) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(bottom = extraKeyboardPadding)
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(
                        value = inputText,
                        onValueChange = { inputText = it },
                        modifier = Modifier
                            .weight(1f)
                            .onPreviewKeyEvent { event ->
                                val isEnter = event.key == Key.Enter || event.key == Key.NumPadEnter
                                if (event.type == KeyEventType.KeyDown && isEnter && !event.isShiftPressed) {
                                    handleSend()
                                    true
                                } else {
                                    false
                                }
                            },
                        placeholder = {
                            Text(if (pendingConfirmation == "yes_no") "Yes, no, or something else…" else "Message Nova…")
                        },
                        maxLines = 4,
                        shape = RoundedCornerShape(24.dp),
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                        keyboardActions = KeyboardActions(onSend = { handleSend() }),
                    )
                    Spacer(Modifier.width(8.dp))
                    IconButton(
                        onClick = { handleSend() },
                        enabled = inputText.isNotBlank(),
                    ) {
                        Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send")
                    }
                }
                Spacer(Modifier.height(8.dp))
                FilledIconButton(
                    onClick = { onMicClick() },
                    enabled = voiceState == VoiceState.IDLE || voiceState == VoiceState.LISTENING,
                    shape = RoundedCornerShape(24.dp),
                    colors = IconButtonDefaults.filledIconButtonColors(
                        containerColor = NovaMicPurple,
                        contentColor = Color.White,
                        disabledContainerColor = NovaMicPurple.copy(alpha = 0.4f),
                        disabledContentColor = Color.White,
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(56.dp),
                ) {
                    Icon(
                        if (voiceState == VoiceState.LISTENING) Icons.Default.Stop else Icons.Default.Mic,
                        contentDescription = if (voiceState == VoiceState.LISTENING) "Stop" else "Speak",
                    )
                }
            }
        }
    }
}
