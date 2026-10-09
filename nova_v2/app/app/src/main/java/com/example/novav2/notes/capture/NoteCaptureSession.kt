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

    // Loudness of what the transcriber was fed - see [levelSummary].
    private var peak = 0
    private var sumSquares = 0.0
    private var sampleCount = 0L

    fun onPcm(pcm: ShortArray, adpcm: ByteArray) {
        for (s in pcm) {
            val v = s.toInt()
            val a = if (v < 0) -v else v
            if (a > peak) peak = a
            sumSquares += v.toDouble() * v
        }
        sampleCount += pcm.size
        transcriber.accept(pcm)
        audio?.write(adpcm)
    }

    /** For the log line after each recording. Speech Vosk can use typically peaks in the
     * thousands with rms in the hundreds; peak under ~300 is near-silence, and a peak pinned at
     * 32767 means the audio is clipping. */
    fun levelSummary(): String {
        val rms = if (sampleCount == 0L) 0.0 else kotlin.math.sqrt(sumSquares / sampleCount)
        return "peak $peak / 32767, rms ${rms.toInt()}"
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
