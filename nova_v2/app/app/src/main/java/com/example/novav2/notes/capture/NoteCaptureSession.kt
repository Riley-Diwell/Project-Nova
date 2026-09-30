package com.example.novav2.notes.capture

import android.content.Context
import com.example.novav2.ble.RecordingMode
import com.example.novav2.notes.CapturedNote
import com.example.novav2.notes.audio.NoteAudioStore
import com.example.novav2.notes.audio.NoteAudioWriter
import com.example.novav2.state.CalendarSignal
import com.example.novav2.stt.TranscriberSession
import com.example.novav2.stt.TranscriptResult
import java.util.UUID

/**
 * One recording from the device, from its START frame to its END: streams the audio into the
 * transcriber, remembers which calendar event
 * was running, and (if the user opted in) keeps the compressed audio on the phone.
 *
 * Single-threaded: [NovaDeviceService]'s recording router drives every call from one coroutine.
 */
class NoteCaptureSession(
    context: Context,
    val mode: RecordingMode,
    private val transcriber: TranscriberSession,
    val startedAtMillis: Long = System.currentTimeMillis(),
) {
    private val appContext = context.applicationContext

    /** The note's id from the start, so kept audio can be filed under it before it exists. */
    val noteId: String = UUID.randomUUID().toString()

    // Captured at the start: a note that runs past its calendar slot still belongs to it.
    private val calendarTitle: String? =
        runCatching { CalendarSignal.snapshot(appContext)?.currentEvents?.firstOrNull()?.title }.getOrNull()

    // Commands are never kept - see NoteAudioStore.
    private val audio: NoteAudioWriter? =
        if (mode == RecordingMode.HOLD) null else NoteAudioStore.newWriter(appContext, noteId)

    fun onPcm(pcm: ShortArray, adpcm: ByteArray) {
        transcriber.accept(pcm)
        audio?.write(adpcm)
    }

    /** Transcribes whatever is left and returns the result - suspends if the STT model is still
     * loading (the audio is held on disk meanwhile). */
    suspend fun finish(): TranscriptResult {
        audio?.close()
        val result = transcriber.finish()
        if (result.isBlank) audio?.discard()
        return result
    }

    fun cancel() {
        transcriber.cancel()
        audio?.discard()
    }

    /** The note this recording becomes, for a note mode. [text] overrides the transcript (a
     * "note …" prefix stripped off a plain hold). */
    fun toCapturedNote(result: TranscriptResult, truncated: Boolean, text: String = result.text): CapturedNote {
        val kind = when (mode) {
            RecordingMode.NOTE -> CapturedNote.KIND_DICTATION
            RecordingMode.HOLD -> CapturedNote.KIND_QUICK
        }
        return CapturedNote(
            id = noteId,
            createdAtMillis = startedAtMillis,
            source = CapturedNote.SOURCE_DEVICE,
            kind = kind,
            text = text.trim(),
            // A quick note is one sentence - its segments would just repeat the text.
            segments = if (kind == CapturedNote.KIND_QUICK) emptyList()
                else result.segments.map { CapturedNote.Segment(it.startS, it.endS, it.text) },
            durationS = result.durationS,
            calendarTitle = calendarTitle,
            sttEngine = result.engine,
            sttAvgConf = result.avgConfidence,
            truncated = truncated,
        )
    }
}
