package com.example.novav2.stt

/**
 * Speech-to-text for one recording, fed as it arrives. A seam, so the engine can change:
 * Vosk small today ([StreamingTranscriber]), and whatever the WER measurement picks later
 * (Vosk lgraph, on-phone whisper.cpp) without the capture code changing.
 */
interface Transcriber {
    fun newSession(): TranscriberSession
}

interface TranscriberSession {
    /** One block of 16 kHz mono PCM, in order. Must not block for long - it is called from the
     * single coroutine draining the device's audio frames. */
    fun accept(samples: ShortArray)

    /** No more audio. Returns everything heard. Suspends if the model is still loading - the
     * audio is kept (on disk, not in RAM) until it can be transcribed. */
    suspend fun finish(): TranscriptResult

    /** Throw the session away without transcribing (the recording is being discarded). */
    fun cancel()
}

/** One stretch of speech between two pauses, with where it sits in the recording. */
data class TranscriptSegment(val startS: Double, val endS: Double, val text: String)

data class TranscriptResult(
    val text: String,
    val segments: List<TranscriptSegment>,
    /** Mean per-word confidence, 0..1; null if the engine reported none. */
    val avgConfidence: Double?,
    val engine: String,
    /** Audio length, from the samples received - not wall-clock, which a lagging link stretches. */
    val durationS: Double,
) {
    val isBlank: Boolean get() = text.isBlank()
}
