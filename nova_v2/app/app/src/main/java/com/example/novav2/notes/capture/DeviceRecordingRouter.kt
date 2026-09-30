package com.example.novav2.notes.capture

import android.content.Context
import android.util.Log
import com.example.novav2.ble.AudioFrame
import com.example.novav2.ble.NovaDeviceRepository
import com.example.novav2.ble.RecordingMode
import com.example.novav2.data.NovaDatabase
import com.example.novav2.data.toEntity
import com.example.novav2.model.ChatMessage
import com.example.novav2.notes.CapturedNote
import com.example.novav2.notes.NoteNotifier
import com.example.novav2.notes.NoteRouter
import com.example.novav2.notes.NoteSubmitResult
import com.example.novav2.notes.NotesRepository
import com.example.novav2.notes.audio.NoteAudioStore
import com.example.novav2.state.AppForegroundState
import com.example.novav2.stt.Transcriber
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val TAG = "DeviceRecordingRouter"

/**
 * Where each recording from the device goes, by the mode its START frame carries:
 *
 *   HOLD  -> "note …" prefix?  a quick note, saved verbatim and silently
 *                               otherwise the assistant, as before
 *   NOTE  -> a dictated note (no prefix needed), summarised if long
 *
 * Voice notes never go through /event: they are POSTed straight to /notes, so a dictation's
 * sentences never reach the episode log or the statement pass. Feedback is haptic,
 * never speech - one buzz saved, two quick buzzes when a summary is ready, two long buzzes if
 * something went wrong (the note is then kept on the phone and a notification says so).
 */
class DeviceRecordingRouter(
    context: Context,
    private val scope: CoroutineScope,
    private val transcriber: Transcriber,
    /** A plain hold that wasn't a note - hand the words to the assistant. */
    private val onCommand: (String) -> Unit,
) {
    private val appContext = context.applicationContext
    private val repository = NotesRepository(appContext)

    @Volatile private var current: NoteCaptureSession? = null

    /** Drains the device's ordered audio stream. Runs until the flow completes or the scope is
     * cancelled; decoding happens here, off the main thread. */
    suspend fun run(frames: Flow<AudioFrame>) = withContext(Dispatchers.Default) {
        frames.collect { frame ->
            when (frame) {
                is AudioFrame.Start -> {
                    current?.cancel()  // an unterminated predecessor - the assembler prevents this
                    current = NoteCaptureSession(appContext, frame.mode, transcriber.newSession(), frame.startedAtMillis)
                    Log.i(TAG, "recording started: ${frame.mode}")
                }
                is AudioFrame.Pcm -> current?.onPcm(frame.samples, frame.adpcm)
                is AudioFrame.End -> {
                    val session = current ?: return@collect
                    current = null
                    // Finishing (final decode, network) must not hold up the next recording's
                    // frames, so it runs alongside the collector.
                    scope.launch(Dispatchers.Default) { finish(session, frame.truncated) }
                }
            }
        }
    }

    private suspend fun finish(session: NoteCaptureSession, truncated: Boolean) {
        val result = try {
            session.finish()
        } catch (e: Exception) {
            Log.e(TAG, "transcription failed", e)
            session.cancel()
            if (session.mode != RecordingMode.HOLD) failureBuzz()
            return
        }
        Log.i(TAG, "recording finished: ${session.mode}, ${result.durationS}s, ${result.text.length} chars")

        when (session.mode) {
            RecordingMode.HOLD -> {
                if (result.isBlank) return
                val body = NoteRouter.match(result.text)
                if (body == null) {
                    withContext(Dispatchers.Main) { onCommand(result.text) }
                } else {
                    save(session.toCapturedNote(result, truncated, text = body))
                }
            }
            RecordingMode.NOTE -> {
                if (result.isBlank) {
                    // Nothing intelligible - there is no note to keep, but the user pressed
                    // for one, so say so rather than staying silent.
                    failureBuzz()
                    return
                }
                save(session.toCapturedNote(result, truncated))
            }
        }
    }

    private suspend fun save(note: CapturedNote) {
        when (val outcome = repository.submit(note)) {
            is NoteSubmitResult.Saved -> {
                savedBuzz()
                chatBubble("Note saved: ${note.text.take(80)}")
                if (NotesRepository.wantsSummary(note)) {
                    repository.summarise(note.id)?.let { saved ->
                        summaryBuzz()
                        if (!withContext(Dispatchers.Main) { AppForegroundState.isInForeground() }) {
                            NoteNotifier.summaryReady(appContext, saved)
                        }
                    }
                }
            }
            is NoteSubmitResult.Queued -> {
                failureBuzz()
                NoteNotifier.queued(appContext, note.text)
            }
            is NoteSubmitResult.Rejected -> {
                failureBuzz()
                NoteAudioStore.delete(appContext, note.id)
                NoteNotifier.failed(appContext, note.text, outcome.reason)
            }
        }
    }

    /** The Voice tab's record that it happened - the phone stays in the bag, but later the
     * thread shows it. */
    private suspend fun chatBubble(text: String) {
        runCatching {
            NovaDatabase.getInstance(appContext).chatMessageDao()
                .insert(ChatMessage(text = text, fromUser = false).toEntity(System.currentTimeMillis()))
        }
    }

    // --- haptics, through the phone's own commands to the device -----------------------

    private fun buzz(ms: Int) = NovaDeviceRepository.commandSender.value?.sendHapticPulse(ms)

    private fun savedBuzz() = buzz(150)

    private suspend fun summaryBuzz() {
        buzz(80); delay(240); buzz(80)
    }

    private suspend fun failureBuzz() {
        buzz(400); delay(700); buzz(400)
    }
}
