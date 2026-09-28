package com.example.novav2.notes

import android.content.Context
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.example.novav2.knowledge.KnowledgeRepository
import com.example.novav2.network.NotesApiClient
import com.example.novav2.notes.audio.NoteAudioStore
import com.example.novav2.notes.data.NoteRowEntity
import com.example.novav2.notes.data.NotesDatabase
import com.example.novav2.notes.data.PendingNoteEntity
import java.io.IOException
import java.time.Instant
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private const val TAG = "NotesRepository"

/**
 * The phone's single way to store a note ([NoteSink]) and the owner of the outbox.
 *
 * Every note is written to the outbox *first*, then sent. A note only leaves the outbox once
 * the server has it - so a crash, a dead network or a Cloud Run cold start in the middle loses
 * nothing; [NoteOutboxWorker] retries with backoff whenever there is a network.
 *
 * Also keeps the Notes list's cache ([rows]): the list reads it, [syncList] refreshes it from the
 * server, and the user's own changes write to it directly. Wiped on sign-out with the outbox.
 */
class NotesRepository(context: Context) : NoteSink {
    private val appContext = context.applicationContext
    private val outbox = NotesDatabase.getInstance(appContext).pendingNoteDao()
    private val cache = NotesDatabase.getInstance(appContext).noteRowDao()

    /** The cached list, newest first. Emits on every change to it. */
    val rows: Flow<List<NotesApiClient.NoteRow>> = cache.observe().map { it.map(NoteRowEntity::toRow) }

    /** Replaces the cached list with the server's. Throws IOException, leaving the cache as it was. */
    suspend fun syncList() {
        cache.replaceAll(NotesApiClient.list().map { it.toEntity() })
    }

    /** One note as it now stands - a fresh save or the result of an edit. */
    suspend fun cache(row: NotesApiClient.NoteRow) = cache.upsert(listOf(row.toEntity()))

    override suspend fun submit(note: CapturedNote): NoteSubmitResult {
        if (note.text.isBlank()) return NoteSubmitResult.Rejected("nothing was heard")

        val payload = with(NotesApiClient) { note.toJson() }.toString()
        outbox.upsert(PendingNoteEntity(
            id = note.id,
            payload = payload,
            createdAtMillis = note.createdAtMillis,
            wantsSummary = wantsSummary(note),
            preview = note.text.take(PREVIEW_CHARS),
        ))

        return try {
            NotesApiClient.create(note)
            outbox.delete(note.id)
            NoteSubmitResult.Saved(note.id)
        } catch (e: NotesApiClient.NotesHttpException) {
            if (e.retryable) queue(note.id, e) else {
                Log.w(TAG, "server rejected note ${note.id}: ${e.message}")
                outbox.delete(note.id)
                NoteSubmitResult.Rejected(e.message ?: "rejected")
            }
        } catch (e: IOException) {
            queue(note.id, e)
        }
    }

    /** Asks the server for a summary of a long note. Returns the updated note, or null if it
     * failed (the note is still saved; the detail view offers Re-summarise). */
    suspend fun summarise(noteId: String): NotesApiClient.Note? = try {
        NotesApiClient.summarise(noteId).takeIf { it.summaryStatus == "done" }
    } catch (e: IOException) {
        Log.w(TAG, "summary for $noteId failed: ${e.message}")
        null
    }

    /** Deletes on the server and, with it, any audio kept on this phone for the note - and the
     * beliefs promoted from it, which the server deletes too. */
    suspend fun delete(noteId: String) {
        NotesApiClient.delete(noteId)
        KnowledgeRepository.onNoteDeleted(noteId)
        cache.delete(noteId)
        outbox.delete(noteId)
        NoteAudioStore.delete(appContext, noteId)
    }

    suspend fun pendingCount(): Int = outbox.count()

    private suspend fun queue(noteId: String, e: Exception): NoteSubmitResult {
        Log.w(TAG, "note $noteId queued: ${e.message}")
        outbox.recordFailure(noteId, e.message ?: e.javaClass.simpleName)
        scheduleOutboxDrain(appContext)
        return NoteSubmitResult.Queued(noteId, e.message ?: "offline")
    }

    companion object {
        /** Mirrors the server's SUMMARY_MIN_WORDS (notes_pipeline/summarise.py). */
        const val SUMMARY_MIN_WORDS = 150
        private const val PREVIEW_CHARS = 120
        private const val OUTBOX_WORK = "nova-notes-outbox"

        fun wantsSummary(note: CapturedNote): Boolean =
            note.summarise && note.text.split(Regex("\\s+")).count { it.isNotBlank() } >= SUMMARY_MIN_WORDS

        /** Drain the outbox as soon as there is a network, retrying with backoff. KEEP: one
         * drain at a time is enough - it takes everything that is pending. */
        fun scheduleOutboxDrain(context: Context) {
            val request = OneTimeWorkRequestBuilder<NoteOutboxWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context)
                .enqueueUniqueWork(OUTBOX_WORK, ExistingWorkPolicy.KEEP, request)
        }
    }
}

private fun NotesApiClient.NoteRow.toEntity() = NoteRowEntity(
    id = id, createdAtMillis = createdAt.toEpochMilli(), source = source, kind = kind, title = title,
    preview = preview, tldr = tldr, durationS = durationS, calendarTitle = calendarTitle,
    summaryStatus = summaryStatus, promoted = promoted,
)

private fun NoteRowEntity.toRow() = NotesApiClient.NoteRow(
    id = id, createdAt = Instant.ofEpochMilli(createdAtMillis), source = source, kind = kind, title = title,
    preview = preview, snippet = null, snippetStartS = null, tldr = tldr, durationS = durationS,
    calendarTitle = calendarTitle, summaryStatus = summaryStatus, promoted = promoted,
)
