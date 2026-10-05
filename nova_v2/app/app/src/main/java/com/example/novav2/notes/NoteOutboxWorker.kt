package com.example.novav2.notes

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.example.novav2.network.NotesApiClient
import com.example.novav2.notes.audio.NoteAudioStore
import com.example.novav2.notes.data.NotesDatabase
import com.example.novav2.notes.data.PendingNoteDeleteEntity
import com.example.novav2.stt.StreamingTranscriber
import java.io.IOException

/**
 * Sends everything in the outbox (see [NotesRepository]), then every delete waiting to reach the
 * server. Retries the whole drain with WorkManager's backoff if anything is still pending because
 * the server is unreachable; drops a note only when the server has definitively rejected it (a
 * 4xx), and says so.
 *
 * Creates go before deletes, so a note deleted while its upload was in flight is deleted on the
 * server after that upload lands, never before it (where the upload would bring it back).
 */
class NoteOutboxWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        // Also the natural moment to expire opted-in audio past its retention window.
        NoteAudioStore.purgeExpired(applicationContext)
        // ...and to clear speech-to-text spools a crashed capture left behind.
        StreamingTranscriber.purgeOrphanSpools(applicationContext)

        val database = NotesDatabase.getInstance(applicationContext)
        val outbox = database.pendingNoteDao()
        val deletes = database.pendingNoteDeleteDao()
        val repository = NotesRepository(applicationContext)
        var retry = false

        for (pending in outbox.all()) {
            try {
                NotesApiClient.createRaw(pending.payload)
                outbox.delete(pending.id)
                // Deleted while it was uploading: the delete below takes it straight back off.
                if (deletes.isPending(pending.id) > 0) continue
                NoteNotifier.savedLater(applicationContext, pending.id, pending.preview)
                if (pending.wantsSummary) {
                    repository.summarise(pending.id)?.let { note ->
                        NoteNotifier.summaryReady(applicationContext, note)
                    }
                }
            } catch (e: NotesApiClient.NotesHttpException) {
                if (e.retryable) {
                    outbox.recordFailure(pending.id, e.message ?: "server error")
                    retry = true
                } else {
                    Log.w(TAG, "outbox: server rejected ${pending.id}: ${e.message}")
                    outbox.delete(pending.id)
                    NoteNotifier.failed(applicationContext, pending.id, pending.preview, e.message ?: "rejected")
                }
            } catch (e: IOException) {
                outbox.recordFailure(pending.id, e.message ?: "offline")
                retry = true
            }
        }

        for (delete in deletes.all()) {
            try {
                if (delete.id == PendingNoteDeleteEntity.ALL) NotesApiClient.deleteAll()
                else NotesApiClient.delete(delete.id)  // a 404 counts as done
                deletes.delete(delete.id)
            } catch (e: NotesApiClient.NotesHttpException) {
                if (e.retryable) retry = true else {
                    // Nothing more this phone can do; nothing of the note is left on it.
                    Log.w(TAG, "outbox: server refused delete of ${delete.id}: ${e.message}")
                    deletes.delete(delete.id)
                }
            } catch (e: IOException) {
                retry = true
            }
        }
        return if (retry) Result.retry() else Result.success()
    }

    private companion object {
        const val TAG = "NoteOutboxWorker"
    }
}
