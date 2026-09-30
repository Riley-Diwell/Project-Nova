package com.example.novav2.notes

import java.util.UUID

/**
 * A note the phone captured and wants stored, mirroring the
 * server's POST /notes body. Capture code (device voice, typed notes) only ever produces
 * these; [NoteSink] owns the outbox, the network and the retries.
 *
 * [id] is generated here and is the server's idempotency key, so the outbox can retry a POST
 * that may already have landed without creating a duplicate.
 */
data class CapturedNote(
    val id: String = UUID.randomUUID().toString(),
    val createdAtMillis: Long = System.currentTimeMillis(),
    /** "device_voice" | "phone_voice" | "typed" */
    val source: String,
    /** "quick" | "dictation" */
    val kind: String,
    val text: String,
    val title: String? = null,
    val segments: List<Segment> = emptyList(),
    val durationS: Double? = null,
    val calendarTitle: String? = null,
    val location: String? = null,
    val sttEngine: String? = null,
    val sttAvgConf: Double? = null,
    val tags: List<String> = emptyList(),
    /** Summarise once stored? Quick notes never are (the text is the content). */
    val summarise: Boolean = kind != KIND_QUICK,
    /** Set when recording ended early (the link dropped) - shown, not sent. */
    val truncated: Boolean = false,
) {
    data class Segment(val startS: Double, val endS: Double, val text: String)

    companion object {
        const val KIND_QUICK = "quick"
        const val KIND_DICTATION = "dictation"
        const val SOURCE_DEVICE = "device_voice"
        const val SOURCE_TYPED = "typed"
    }
}

/** What happened to a submitted note, for the capture side's feedback (buzz, bubble). */
sealed class NoteSubmitResult {
    /** Stored on the server. */
    data class Saved(val noteId: String) : NoteSubmitResult()
    /** Couldn't reach the server; kept in the outbox and retried in the background. */
    data class Queued(val noteId: String, val reason: String) : NoteSubmitResult()
    /** The server refused it and retrying won't help (e.g. empty text). Dropped. */
    data class Rejected(val reason: String) : NoteSubmitResult()
}

/** Where captured notes go - implemented by [NotesRepository]. */
interface NoteSink {
    suspend fun submit(note: CapturedNote): NoteSubmitResult
}
