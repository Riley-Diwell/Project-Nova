package com.example.novav2.notes

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.example.novav2.network.NotesApiClient
import com.example.novav2.notes.audio.NoteAudioStore
import com.example.novav2.notes.data.NotesDatabase
import java.io.IOException

/**
 * Sends everything in the outbox (see [NotesRepository]). Retries the whole drain with
 * WorkManager's backoff if anything is still pending because the server is unreachable; drops
 * a note only when the server has definitively rejected it (a 4xx), and says so.
 */
class NoteOutboxWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        // Also the natural moment to expire opted-in audio past its retention window.
        NoteAudioStore.purgeExpired(applicationContext)

        val outbox = NotesDatabase.getInstance(applicationContext).pendingNoteDao()
        val repository = NotesRepository(applicationContext)
        var retry = false

        for (pending in outbox.all()) {
            try {
                NotesApiClient.createRaw(pending.payload)
                outbox.delete(pending.id)
                NoteNotifier.savedLater(applicationContext, pending.preview)
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
                    NoteNotifier.failed(applicationContext, pending.preview, e.message ?: "rejected")
                }
            } catch (e: IOException) {
                outbox.recordFailure(pending.id, e.message ?: "offline")
                retry = true
            }
        }
        return if (retry) Result.retry() else Result.success()
    }

    private companion object {
        const val TAG = "NoteOutboxWorker"
    }
}
