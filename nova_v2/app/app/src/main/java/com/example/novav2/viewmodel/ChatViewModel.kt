package com.example.novav2.viewmodel

import android.app.Application
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.example.novav2.data.NovaDatabase
import com.example.novav2.data.toChatMessage
import com.example.novav2.data.toEntity
import com.example.novav2.model.ChatMessage
import com.example.novav2.network.NotesApiClient
import com.example.novav2.network.NovaApiClient
import com.example.novav2.network.SavedAction
import com.example.novav2.notes.RecallChips
import com.example.novav2.state.CalendarWriter
import com.example.novav2.state.ReminderRepository
import com.example.novav2.state.TurnActionApplier
import com.example.novav2.state.UserStateCollector
import com.example.novav2.state.parseIsoToEpochMillis
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.IOException
import java.time.format.DateTimeParseException
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

enum class VoiceState { IDLE, LISTENING, THINKING, SPEAKING }

/**
 * Owns the Voice screen's whole turn lifecycle - the message thread, the in-flight request, the
 * listening/thinking/speaking state, TextToSpeech and the SpeechRecognizer - so none of it is torn
 * down when the user switches tabs. [VoiceScreen][com.example.novav2.ui.screens.VoiceScreen] used
 * to hold all of this in `remember` state and `rememberCoroutineScope`, which meant leaving the tab
 * mid-turn cancelled the request and dropped the "Thinking…" bubble along with the reply. The
 * screen is now only a view onto this; it is scoped to the Activity so it outlives the tab.
 *
 * Messages are mirrored to [NovaDatabase] and collected from [ChatMessageDao.observeAll] rather than
 * loaded once: [com.example.novav2.service.AssistVoiceService] writes turns headlessly, and those
 * need to show up here too, not just whatever existed when this ViewModel was first created.
 *
 * What still has to live in the screen is anything that needs an Activity: the permission
 * launchers. Calendar actions that arrive without permission wait in [pendingCalendarActions] /
 * [pendingEditActions] until the screen is showing to ask for it.
 */
class ChatViewModel(application: Application) : AndroidViewModel(application) {
    private val app: Application = application
    private val dao = NovaDatabase.getInstance(application).chatMessageDao()
    private val prefs = application.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    private val mainHandler = Handler(Looper.getMainLooper())

    val messages: SnapshotStateList<ChatMessage> = mutableStateListOf()

    var voiceState by mutableStateOf(VoiceState.IDLE)
        private set
    /** A turn in flight or a one-off notice - rendered as the status bubble under the thread. */
    var statusText by mutableStateOf("")
        private set
    /** The unsent draft - kept here so it survives a tab switch too. */
    var inputText by mutableStateOf("")

    /** When true, replies are shown but not read out. Remembered across launches. */
    var muted by mutableStateOf(prefs.getBoolean(KEY_MUTED, false))
        private set

    // Mirrors the last reply's EventResult.Final.confirmation - "yes_no" shows the quick-reply
    // buttons; cleared as soon as any new turn is sent (button tap, typed, or spoken), same as
    // the backend's own _PENDING_CONFIRMATION is popped on the next voice turn.
    var pendingConfirmation by mutableStateOf<String?>(null)
        private set
    // The notes the last reply's memory recall answered from - chips that open them.
    // Cleared when the next turn starts.
    var recallChips by mutableStateOf<List<NotesApiClient.NoteRow>>(emptyList())
        private set
    // What the last reply saved, and where - a note, a memory or a reminder, each with its own
    // chip so the three never look alike. Cleared when the next turn starts.
    var savedChips by mutableStateOf<List<SavedAction>>(emptyList())
        private set
    // A finished turn's calendar.create_event / edit_calendar_event actions, held while
    // WRITE_CALENDAR hasn't been granted yet - the screen asks, then calls
    // [onCalendarPermissionResult]. Separate lists so each goes to the right CalendarWriter call.
    var pendingCalendarActions by mutableStateOf<List<NovaApiClient.CalendarAction>>(emptyList())
        private set
    var pendingEditActions by mutableStateOf<List<NovaApiClient.EditCalendarAction>>(emptyList())
        private set
    // delete_calendar_event Actions waiting on the user's explicit Yes/No - the hard gate: unlike
    // the two lists above (which only wait on a permission prompt and then write
    // unconditionally), nothing here is ever deleted without that confirmation, regardless of
    // gain or what the model said in speech.
    var pendingDeleteConfirmations by mutableStateOf<List<NovaApiClient.DeleteCalendarAction>>(emptyList())
        private set

    // The Episode of the reply currently being spoken, held until its Outcome is reported.
    // Atomic and single-shot (getAndSet(null)): onDone and the stop button race whenever the
    // user cuts NOVA off near the end of an utterance, and the turn must be scored once, as
    // whichever got there first.
    private val speakingEpisode = AtomicReference<String?>(null)

    // TextToSpeech initialises asynchronously, and speak() before that finishes is dropped
    // silently - it returns ERROR and says nothing. Track readiness, and hold the one utterance
    // that arrived too early so it can be spoken on init instead of lost.
    private val ttsReady = AtomicBoolean(false)
    private var pendingUtterance: String? = null
    private val textToSpeech: TextToSpeech = TextToSpeech(application) { status ->
        if (status == TextToSpeech.SUCCESS) {
            ttsReady.set(true)
            mainHandler.post {
                pendingUtterance?.let { queued ->
                    pendingUtterance = null
                    speakNow(queued)
                }
            }
        } else {
            mainHandler.post {
                statusText = "Text-to-speech didn't start on this device."
                voiceState = VoiceState.IDLE
            }
        }
    }

    private val speechRecognizer: SpeechRecognizer? =
        if (SpeechRecognizer.isRecognitionAvailable(application)) {
            SpeechRecognizer.createSpeechRecognizer(application)
        } else {
            null
        }

    init {
        viewModelScope.launch {
            dao.observeAll().collect { entities ->
                messages.clear()
                messages.addAll(entities.map { it.toChatMessage() })
            }
        }

        // Once nothing is in flight, whatever statusText still says is a one-off notice ("Didn't
        // catch that", "Stopped.", …) rather than live progress - let it fade instead of sitting
        // at the bottom of the transcript until the next turn. Restarts if the text or state changes.
        viewModelScope.launch {
            snapshotFlow { statusText to voiceState }.collectLatest { (text, state) ->
                if (state == VoiceState.IDLE && text.isNotBlank()) {
                    delay(4000)
                    statusText = ""
                }
            }
        }

        textToSpeech.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {}
            override fun onDone(utteranceId: String?) {
                // Heard out to the end. Silence is the accept signal - the user had a stop
                // button and did not reach for it.
                reportOutcome(accepted = true)
                mainHandler.post { if (voiceState == VoiceState.SPEAKING) voiceState = VoiceState.IDLE }
            }

            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) {
                // The engine failed, which says nothing about whether the user wanted this.
                // Drop the turn rather than scoring it either way.
                speakingEpisode.set(null)
                mainHandler.post { if (voiceState == VoiceState.SPEAKING) voiceState = VoiceState.IDLE }
            }
        })
    }

    override fun onCleared() {
        textToSpeech.stop()
        textToSpeech.shutdown()
        speechRecognizer?.destroy()
    }

    fun addMessage(text: String, fromUser: Boolean): ChatMessage {
        val message = ChatMessage(text = text, fromUser = fromUser)
        messages.add(message)
        val timestamp = System.currentTimeMillis()
        viewModelScope.launch(Dispatchers.IO) {
            dao.insert(message.toEntity(timestamp))
        }
        return message
    }

    fun clearMessages() {
        messages.clear()
        // A leftover notice ("Didn't catch that", "Stopped.") belongs to the chat being cleared;
        // one describing a turn still in flight does not.
        if (voiceState == VoiceState.IDLE) statusText = ""
        viewModelScope.launch(Dispatchers.IO) {
            dao.clearAll()
        }
    }

    fun showNotice(text: String) {
        statusText = text
    }

    /**
     * Muting mid-reply cuts the speech but, unlike [stopSpeaking], isn't scored as a rejection -
     * it's a choice about the medium, not a judgement on what Nova said.
     */
    fun toggleMuted() {
        muted = !muted
        prefs.edit().putBoolean(KEY_MUTED, muted).apply()
        if (muted) {
            pendingUtterance = null
            if (voiceState == VoiceState.SPEAKING) {
                speakingEpisode.set(null)
                textToSpeech.stop()
                voiceState = VoiceState.IDLE
            }
        }
    }

    private fun reportOutcome(accepted: Boolean) {
        val episodeId = speakingEpisode.getAndSet(null) ?: return
        viewModelScope.launch { NovaApiClient.postOutcome(episodeId, accepted) }
    }

    /**
     * Cuts NOVA off mid-sentence. The barge-in that DESIGN.md §5.7 reads as the user's
     * rejection of the turn - and, first and foremost, the control any talking assistant owes
     * its user. It is feedback precisely because it is not a feedback button.
     */
    fun stopSpeaking() {
        pendingUtterance = null
        textToSpeech.stop()
        reportOutcome(accepted = false)
        voiceState = VoiceState.IDLE
        statusText = "Stopped."
    }

    private fun speak(text: String, episodeId: String?) {
        speakingEpisode.set(episodeId)
        if (text.isBlank()) {
            // The backend returns an empty string when the Intent Surface ends without a final
            // answer. Saying nothing looks identical to a crash from the user's side, so say so
            // instead.
            statusText = "Nova didn't have an answer for that - tap to try again."
            voiceState = VoiceState.IDLE
            // Nothing was said, so there is nothing for the user to accept or reject. Scoring
            // this would blame the tools for an empty reply.
            speakingEpisode.set(null)
            return
        }
        if (muted) {
            // Shown but not heard - no stop button was ever on offer, so there's no accept or
            // reject signal to score either.
            voiceState = VoiceState.IDLE
            speakingEpisode.set(null)
            return
        }

        // Anything past the engine's limit is rejected outright, not truncated - and a long
        // recall ("what have I asked you to remember?") is exactly the kind of answer that gets
        // near it.
        val limit = TextToSpeech.getMaxSpeechInputLength()
        val utterance = if (text.length > limit) text.take(limit) else text

        voiceState = VoiceState.SPEAKING
        if (!ttsReady.get()) {
            pendingUtterance = utterance
            return
        }
        speakNow(utterance)
    }

    private fun speakNow(utterance: String) {
        val result = textToSpeech.speak(utterance, TextToSpeech.QUEUE_FLUSH, null, "nova-response")
        if (result != TextToSpeech.SUCCESS) {
            statusText = "Couldn't speak that - tap to try again."
            voiceState = VoiceState.IDLE
            speakingEpisode.set(null)
        }
    }

    /**
     * A failed turn, told the same way a successful one is - added to the transcript and spoken -
     * rather than left as a caption underneath that a narrow screen truncates to something like
     * "Couldn't reach the back…". No episodeId: a failed turn is evidence about the network or the
     * clock, never about the tools, so there is nothing here for [reportOutcome] to score.
     */
    private fun sayFailure(text: String) {
        statusText = ""
        addMessage(text, fromUser = false)
        speak(text, episodeId = null)
    }

    /** Shared by the send button, the IME "send" action, and a physical Enter key press. */
    fun sendDraft() {
        val text = inputText
        // One turn at a time - the draft stays put until the current reply lands.
        if (text.isBlank() || voiceState == VoiceState.THINKING) return
        inputText = ""
        sendMessage(text)
    }

    /**
     * Sends one turn to the backend, whether it came from typing, a quick-reply button, or a
     * voice transcript. Runs in [viewModelScope], so it keeps going if the user leaves the tab.
     */
    fun sendMessage(text: String) {
        if (text.isBlank()) return
        if (voiceState == VoiceState.SPEAKING) {
            // A new turn over the old reply cuts it off - otherwise it keeps talking through
            // "Thinking…" and its onDone knocks the new turn back to IDLE. Not scored either way:
            // a follow-up isn't clearly a rejection the way tapping Stop is.
            speakingEpisode.set(null)
            pendingUtterance = null
            textToSpeech.stop()
        }
        addMessage(text, fromUser = true)
        pendingConfirmation = null
        recallChips = emptyList()
        savedChips = emptyList()
        voiceState = VoiceState.THINKING
        statusText = "Sending to Nova…"
        viewModelScope.launch {
            val userState = ReminderRepository.attachWindow(app, UserStateCollector.snapshot(app))
            try {
                // The Intent Surface can pause on a client-executed tool (get_calendar_range,
                // get_reminders) it needs on-device data for; TurnActionApplier resolves it
                // locally and hands the result back until there's a final answer.
                val result = TurnActionApplier.resolveNeedMore(
                    app, NovaApiClient.postVoiceEvent(text, userState),
                ) { need ->
                    statusText = if (need.requestType == "get_reminders") "Checking your reminders…"
                    else "Checking your calendar…"
                }
                val finalResult = result as? NovaApiClient.EventResult.Final
                val calendarActions = finalResult?.actions.orEmpty()
                val editActions = finalResult?.editActions.orEmpty()
                if (calendarActions.isNotEmpty() || editActions.isNotEmpty()) {
                    if (CalendarWriter.hasPermission(app)) {
                        writeCalendar(calendarActions, editActions)
                    } else {
                        // Appended so an earlier turn's actions still waiting on the prompt
                        // aren't lost. The screen asks once for both.
                        pendingCalendarActions = pendingCalendarActions + calendarActions
                        pendingEditActions = pendingEditActions + editActions
                    }
                }
                val deleteActions = finalResult?.deleteActions.orEmpty()
                if (deleteActions.isNotEmpty()) {
                    // Appended, not replaced - a dialog already awaiting an earlier turn's answer
                    // must not be dropped by a new one arriving.
                    pendingDeleteConfirmations = pendingDeleteConfirmations + deleteActions
                }
                // Timers, alarms, reminders and the departure alarm - fire-and-forget like the
                // calendar writes above, no prompt needed. Shared with AssistVoiceService.
                finalResult?.let { TurnActionApplier.applyHeadless(app, it) }
                finalResult?.recallActions?.lastOrNull()?.let { recall ->
                    viewModelScope.launch { recallChips = RecallChips.find(recall) }
                }
                savedChips = finalResult?.savedActions.orEmpty()
                val reply = finalResult?.speech ?: "Sorry, I couldn't finish that."
                statusText = ""
                addMessage(reply, fromUser = false)
                pendingConfirmation = finalResult?.confirmation
                speak(reply, finalResult?.episodeId)
            } catch (e: java.net.SocketTimeoutException) {
                // Distinct from "couldn't reach": the backend IS answering, it just took longer
                // than readTimeout. Worth its own message, because the fix is a slower client
                // rather than a broken server.
                sayFailure("Sorry, that's taking too long - can you try again?")
            } catch (e: IOException) {
                sayFailure("Sorry, I couldn't reach the network - can you try again?")
            } catch (e: DateTimeParseException) {
                sayFailure("Sorry, something about that didn't come through right - can you try again?")
            }
        }
    }

    /** The screen's answer to the WRITE_CALENDAR prompt for [pendingCalendarActions]/[pendingEditActions]. */
    fun onCalendarPermissionResult(granted: Boolean) {
        val adds = pendingCalendarActions
        val edits = pendingEditActions
        pendingCalendarActions = emptyList()
        pendingEditActions = emptyList()
        if (granted) viewModelScope.launch { writeCalendar(adds, edits) }
    }

    private suspend fun writeCalendar(
        adds: List<NovaApiClient.CalendarAction>,
        edits: List<NovaApiClient.EditCalendarAction>,
    ) = withContext(Dispatchers.IO) {
        if (adds.isNotEmpty()) writeCalendarActions(app, adds)
        if (edits.isNotEmpty()) writeEditActions(app, edits)
    }

    /** Takes the head of [pendingDeleteConfirmations] off the queue, whatever the answer was. */
    fun dismissDeleteConfirmation() {
        pendingDeleteConfirmations = pendingDeleteConfirmations.drop(1)
    }

    /** Only ever called after the user tapped Delete - see [pendingDeleteConfirmations]. */
    fun deleteCalendarEvent(eventId: Long) {
        viewModelScope.launch(Dispatchers.IO) { CalendarWriter.deleteEvent(app, eventId) }
    }

    /** Assumes RECORD_AUDIO is already granted - the screen checks and prompts first. */
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
                // SpeechRecognizer can report a stray error after it has already delivered
                // results (or after stopListening()) - by then the turn is THINKING/SPEAKING
                // and this must not knock it back to IDLE with a bogus "Didn't catch that".
                if (voiceState != VoiceState.LISTENING) return
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

    /** Force-finishes the utterance now instead of waiting for the recognizer's silence timeout. */
    fun finishListening() {
        statusText = "Sending to Nova…"
        speechRecognizer?.stopListening()
    }

    private companion object {
        const val PREFS = "nova_chat"
        const val KEY_MUTED = "tts_muted"
    }
}

/**
 * Executes the backend's queued "calendar.create_event" actions (add_calendar_event in the
 * server's intent_surface.py) via CalendarWriter, which inserts into the device's Calendar
 * Provider and syncs onward to whichever account owns that calendar (e.g. Google). Assumes
 * WRITE_CALENDAR is already granted - callers must check CalendarWriter.hasPermission first.
 */
private fun writeCalendarActions(
    context: Context,
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
                location = action.location,
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
    context: Context,
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
            location = action.location,
            rrule = action.rrule,
        )
    }
}
