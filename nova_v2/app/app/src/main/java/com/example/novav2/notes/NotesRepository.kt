package com.example.novav2.notes

import android.content.Context
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.example.novav2.data.NovaDatabase
import com.example.novav2.knowledge.KnowledgeRepository
import com.example.novav2.network.NotesApiClient
import com.example.novav2.notes.audio.NoteAudioStore
import com.example.novav2.notes.data.NoteRowEntity
import com.example.novav2.notes.data.NotesDatabase
import com.example.novav2.notes.data.PendingNoteDeleteEntity
import com.example.novav2.notes.data.PendingNoteEntity
import java.io.IOException
import java.time.Instant
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
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
    private val deletes = NotesDatabase.getInstance(appContext).pendingNoteDeleteDao()
    private val chat = NovaDatabase.getInstance(appContext).chatMessageDao()

    /** The cached list, newest first. Emits on every change to it. */
    val rows: Flow<List<NotesApiClient.NoteRow>> = cache.observe().map { it.map(NoteRowEntity::toRow) }

    /** Replaces the cached list with the server's. Throws IOException, leaving the cache as it was. */
    suspend fun syncList() {
        val rows = NotesApiClient.list()
        // The list can be fetched while a delete is still on its way to the server (closing a
        // note after deleting it refreshes the list at once), and would put the note back.
        // Pending deletes are read after the fetch; [deletedHere] covers one that finished in
        // between. A deleted note's id never comes back, so both are safe to filter on.
        val pending = deletes.all().map { it.id }.toSet()
        if (PendingNoteDeleteEntity.ALL in pending) return cache.replaceAll(emptyList())
        cache.replaceAll(rows.filter { it.id !in pending && it.id !in deletedHere }.map { it.toEntity() })
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

    /**
     * Deletes a note entirely (docs/plans/notes-hard-delete-plan.md): everything of it on this
     * phone first - the list row, an unsent copy in the outbox, kept audio, the Voice history
     * bubbles that quote it, its notifications, the beliefs promoted from it - so it is gone from
     * every screen at once, online or not. Then the server, which also deletes the conversation
     * it came from; if that can't be reached the delete waits in [deletes] (ids only) and
     * [NoteOutboxWorker] retries it. Never throws for a network failure.
     */
    suspend fun delete(noteId: String) {
        val unsent = outbox.has(noteId) > 0
        deletedHere.add(noteId)
        deletes.upsert(PendingNoteDeleteEntity(noteId, System.currentTimeMillis()))
        cache.delete(noteId)
        outbox.delete(noteId)
        NoteAudioStore.delete(appContext, noteId)
        chat.deleteForNote(noteId)
        NoteNotifier.cancelFor(appContext, noteId)
        KnowledgeRepository.onNoteDeleted(noteId)
        _deletions.tryEmit(noteId)

        // An unsent note may be uploading right now; deleting it on the server before that
        // upload lands would let the upload bring it back. The outbox drain runs creates before
        // deletes, so leave it to that.
        if (unsent) {
            scheduleOutboxDrain(appContext, afterCurrent = true)
            return
        }
        sendDelete(noteId)
    }

    /** Settings -> Notes -> Delete all notes: [delete] for every note, this phone first. */
    suspend fun deleteAll() {
        deletes.upsert(PendingNoteDeleteEntity(PendingNoteDeleteEntity.ALL, System.currentTimeMillis()))
        cache.clear()
        outbox.clear()
        NoteAudioStore.deleteAll(appContext)
        chat.deleteForAllNotes()
        NoteNotifier.cancelFor(appContext, null)
        KnowledgeRepository.onNoteDeleted(null)
        _deletions.tryEmit(PendingNoteDeleteEntity.ALL)
        sendDelete(PendingNoteDeleteEntity.ALL)
    }

    private suspend fun sendDelete(id: String) {
        try {
            if (id == PendingNoteDeleteEntity.ALL) NotesApiClient.deleteAll() else NotesApiClient.delete(id)
            deletes.delete(id)
            // The server also forgot what it read out of the note's conversation.
            runCatching { KnowledgeRepository.refresh() }
        } catch (e: NotesApiClient.NotesHttpException) {
            if (e.retryable) queueDelete(id, e) else {
                Log.w(TAG, "server refused delete of $id: ${e.message}")
                deletes.delete(id)
            }
        } catch (e: IOException) {
            queueDelete(id, e)
        }
    }

    private fun queueDelete(id: String, e: Exception) {
        Log.w(TAG, "delete of $id queued: ${e.message}")
        scheduleOutboxDrain(appContext)
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

        private val _deletions = MutableSharedFlow<String>(extraBufferCapacity = 16)

        /** Ids of notes deleted on this phone since the app started - ids only, see [syncList]. */
        private val deletedHere: MutableSet<String> = java.util.concurrent.ConcurrentHashMap.newKeySet()

        /** Ids of notes just deleted on this phone ([PendingNoteDeleteEntity.ALL] for every
         * note) - for screens holding a note's words in memory, like the Voice tab's chips. */
        val deletions: SharedFlow<String> = _deletions

        fun wantsSummary(note: CapturedNote): Boolean =
            note.summarise && note.text.split(Regex("\\s+")).count { it.isNotBlank() } >= SUMMARY_MIN_WORDS

        /** Drain the outbox as soon as there is a network, retrying with backoff. KEEP: one
         * drain at a time is enough - it takes everything that is pending. [afterCurrent]
         * instead queues one more drain behind a running one, for a delete that must land after
         * an upload that drain may be in the middle of. */
        fun scheduleOutboxDrain(context: Context, afterCurrent: Boolean = false) {
            val request = OneTimeWorkRequestBuilder<NoteOutboxWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context)
                .enqueueUniqueWork(
                    OUTBOX_WORK,
                    if (afterCurrent) ExistingWorkPolicy.APPEND_OR_REPLACE else ExistingWorkPolicy.KEEP,
                    request,
                )
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
