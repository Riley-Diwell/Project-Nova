package com.example.novav2.auth

import android.content.Context
import com.example.novav2.data.NovaDatabase
import com.example.novav2.knowledge.ConsolidationWorker
import com.example.novav2.knowledge.KnowledgeRepository
import com.example.novav2.network.NotesApiClient
import com.example.novav2.notes.NotesRepository
import com.example.novav2.notes.audio.NoteAudioStore
import com.example.novav2.notes.data.NotesDatabase
import com.example.novav2.notes.data.PendingNoteDeleteEntity
import com.example.novav2.profile.ProfileRepository
import com.example.novav2.state.DepartureUnknownStore
import com.example.novav2.state.ReminderSync
import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * The personal data this phone holds for the signed-in account, and what happens to it when the
 * account changes:
 *
 * - Reminders: synced with the account (ReminderSync). Downloaded on sign-in.
 * - Notes: live on the server already; the phone holds unsent ones (the outbox), a copy of the
 *   list (so the tab opens instantly) and any audio the user opted to keep.
 * - Chat history: this phone only.
 * - Profile: on the server; the phone caches it ([ProfileRepository]).
 * - Knowledge Map: on the server; the phone keeps the last graph in memory ([KnowledgeRepository]).
 *
 * Signing out uploads what it can, then wipes all of it. A session that dies on its own (a
 * revoked refresh token, say) wipes nothing: the data waits, unsent changes included, and goes up
 * when the same person signs back in - or is wiped first if someone else does. [owner] is how
 * the two cases are told apart.
 */
object LocalData {
    private const val PREFS = "nova_local_owner"
    private const val KEY_USER_ID = "user_id"

    /** Right after a sign-in: make sure what's on the phone belongs to [userId], then sync. */
    suspend fun onSignedIn(context: Context, userId: String) {
        val app = context.applicationContext
        val prefs = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        when (prefs.getString(KEY_USER_ID, null)) {
            userId -> Unit
            // First sign-in on this install: whatever reminders it already had (from before
            // accounts existed) become this account's, so upload them all.
            null -> NovaDatabase.getInstance(app).reminderDao().markAllDirty()
            // Someone else's leftovers from a session that expired rather than signed out.
            else -> wipe(app)
        }
        prefs.edit().putString(KEY_USER_ID, userId).apply()
        ReminderSync.schedule(app)
        NotesRepository.scheduleOutboxDrain(app)
        ConsolidationWorker.schedulePeriodic(app)
    }

    /**
     * Before signing out: try to upload every unsent reminder change and note, and send every
     * note delete still waiting. Returns how many are still stuck on the phone (offline, or the
     * server is down) - signing out now loses them, and a stuck delete leaves that note on the
     * server.
     */
    suspend fun flush(context: Context): Int {
        val app = context.applicationContext
        try {
            ReminderSync.syncNow(app)
        } catch (_: IOException) {
        }
        val outbox = NotesDatabase.getInstance(app).pendingNoteDao()
        for (pending in outbox.all()) {
            try {
                NotesApiClient.createRaw(pending.payload)
                outbox.delete(pending.id)
            } catch (_: IOException) {
            }
        }
        val deletes = NotesDatabase.getInstance(app).pendingNoteDeleteDao()
        for (pending in deletes.all()) {
            try {
                if (pending.id == PendingNoteDeleteEntity.ALL) NotesApiClient.deleteAll()
                else NotesApiClient.delete(pending.id)
                deletes.delete(pending.id)
            } catch (_: IOException) {
            }
        }
        return ReminderSync.unsentCount(app) + outbox.count() + deletes.all().size
    }

    /** How many reminder changes and notes haven't reached the account, without trying to send them. */
    suspend fun unsentCount(context: Context): Int {
        val app = context.applicationContext
        val notes = NotesDatabase.getInstance(app)
        return ReminderSync.unsentCount(app) + notes.pendingNoteDao().count() + notes.pendingNoteDeleteDao().all().size
    }

    /** Signing out: forget everything personal this phone keeps. */
    suspend fun wipe(context: Context) {
        val app = context.applicationContext
        NovaDatabase.getInstance(app).chatMessageDao().clearAll()
        ReminderSync.clearLocal(app)
        val outbox = NotesDatabase.getInstance(app).pendingNoteDao()
        outbox.all().forEach { outbox.delete(it.id) }
        NotesDatabase.getInstance(app).noteRowDao().clear()
        NotesDatabase.getInstance(app).pendingNoteDeleteDao().all().forEach {
            NotesDatabase.getInstance(app).pendingNoteDeleteDao().delete(it.id)
        }
        NoteAudioStore.deleteAll(app)
        ProfileRepository.clear(app)
        DepartureUnknownStore.clear(app)
        ConsolidationWorker.cancel(app)
        withContext(Dispatchers.Main) { KnowledgeRepository.clear() }
        app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(KEY_USER_ID).apply()
    }
}
