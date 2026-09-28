package com.example.novav2.state

import android.content.Context
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.example.novav2.auth.AuthRepository
import com.example.novav2.data.NovaDatabase
import com.example.novav2.data.ReminderEntity
import com.example.novav2.network.RemindersApiClient
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Keeps this phone's reminders and the signed-in account's copy in step
 * (server/app/store/reminders.py). The phone still owns reminders day to day - alarms and offline
 * use both work on the Room copy - and every local change is marked `dirty`
 * by [ReminderRepository] and uploaded here.
 *
 * One round trip does both directions: upload the dirty rows, download whatever changed on the
 * account since the last sync ([cursor], kept in prefs). Conflicts go to the later edit, on both
 * ends. Runs as WorkManager work, so it waits for a network and retries with backoff; nothing
 * runs while signed out.
 */
object ReminderSync {
    private const val TAG = "ReminderSync"
    private const val WORK_NAME = "reminder-sync"
    private const val PREFS = "nova_reminder_sync"
    private const val KEY_CURSOR = "cursor"

    private val mutex = Mutex()

    /** Queue a sync for when there's a network. Cheap to call after every change. */
    fun schedule(context: Context) {
        val request = OneTimeWorkRequestBuilder<ReminderSyncWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        // APPEND_OR_REPLACE: a change made while a sync is running still gets its own run after it.
        WorkManager.getInstance(context.applicationContext)
            .enqueueUniqueWork(WORK_NAME, ExistingWorkPolicy.APPEND_OR_REPLACE, request)
    }

    /** Upload and download now. Throws IOException if the server couldn't be reached. */
    suspend fun syncNow(context: Context) = mutex.withLock {
        if (!AuthRepository.isSignedIn) return@withLock
        val app = context.applicationContext
        val prefs = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val dirty = NovaDatabase.getInstance(app).reminderDao().dirty()

        val result = RemindersApiClient.sync(
            since = prefs.getString(KEY_CURSOR, null),
            changes = JSONArray().apply { dirty.forEach { put(it.toWire()) } },
        )
        val fromAccount = result.reminders.mapNotNull { row ->
            try {
                row.getJSONObject("data").toReminder()
            } catch (e: Exception) {
                Log.w(TAG, "skipping unreadable reminder ${row.optString("id")}: $e")
                null
            }
        }
        // Signed out while the request was in flight: the phone's reminders are being (or have
        // been) wiped, so don't write the account's back in.
        if (!AuthRepository.isSignedIn) return@withLock
        ReminderRepository.mergeFromAccount(app, uploaded = dirty, fromAccount = fromAccount)
        prefs.edit().putString(KEY_CURSOR, result.cursor).apply()
    }

    /** How many changes haven't reached the account yet. */
    suspend fun unsentCount(context: Context): Int =
        NovaDatabase.getInstance(context.applicationContext).reminderDao().dirtyCount()

    /** Sign-out: forget this phone's reminders and sync position; the account keeps them. */
    suspend fun clearLocal(context: Context) {
        val app = context.applicationContext
        WorkManager.getInstance(app).cancelUniqueWork(WORK_NAME)
        // Not under [mutex]: a sync stuck on a slow network would hold sign-out up for as long
        // as its timeout. syncNow re-checks the session before merging instead.
        ReminderRepository.clearLocal(app)
        app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply()
    }

    // --- Room <-> wire. `data` is the entity field for field; the server stores it as-is. ---

    private fun ReminderEntity.toWire(): JSONObject = JSONObject()
        .put("id", id)
        .put("status", status)
        .put("updated_at_ms", updatedAtMillis)
        .put("data", JSONObject()
            .put("id", id)
            .put("text", text)
            .put("dueLocal", dueLocal)
            .put("triggerAtMillis", triggerAtMillis)
            .put("status", status)
            .put("priority", priority)
            .put("origin", origin)
            .putOpt("sourceEpisodeId", sourceEpisodeId)
            .put("outcomeReported", outcomeReported)
            .putOpt("deferReason", deferReason)
            .put("deferCount", deferCount)
            .putOpt("firstDeferredAtMillis", firstDeferredAtMillis)
            .put("snoozeCount", snoozeCount)
            .putOpt("previousStatus", previousStatus)
            .putOpt("rebuzzAtMillis", rebuzzAtMillis)
            .putOpt("recurFrequency", recurFrequency)
            .put("recurInterval", recurInterval)
            .putOpt("recurCountRemaining", recurCountRemaining)
            .putOpt("recurUntilLocal", recurUntilLocal)
            .put("createdAtMillis", createdAtMillis)
            .put("updatedAtMillis", updatedAtMillis)
            .putOpt("firedAtMillis", firedAtMillis)
            .putOpt("completedAtMillis", completedAtMillis)
            .putOpt("clearedAtMillis", clearedAtMillis))

    private fun JSONObject.toReminder() = ReminderEntity(
        id = getString("id"),
        text = getString("text"),
        dueLocal = getString("dueLocal"),
        triggerAtMillis = getLong("triggerAtMillis"),
        status = getString("status"),
        priority = getString("priority"),
        origin = getString("origin"),
        sourceEpisodeId = stringOrNull("sourceEpisodeId"),
        outcomeReported = optBoolean("outcomeReported"),
        deferReason = stringOrNull("deferReason"),
        deferCount = optInt("deferCount"),
        firstDeferredAtMillis = longOrNull("firstDeferredAtMillis"),
        snoozeCount = optInt("snoozeCount"),
        previousStatus = stringOrNull("previousStatus"),
        rebuzzAtMillis = longOrNull("rebuzzAtMillis"),
        recurFrequency = stringOrNull("recurFrequency"),
        recurInterval = optInt("recurInterval", 1),
        recurCountRemaining = if (has("recurCountRemaining") && !isNull("recurCountRemaining")) getInt("recurCountRemaining") else null,
        recurUntilLocal = stringOrNull("recurUntilLocal"),
        createdAtMillis = getLong("createdAtMillis"),
        updatedAtMillis = getLong("updatedAtMillis"),
        firedAtMillis = longOrNull("firedAtMillis"),
        completedAtMillis = longOrNull("completedAtMillis"),
        clearedAtMillis = longOrNull("clearedAtMillis"),
    )

    private fun JSONObject.stringOrNull(key: String): String? =
        if (has(key) && !isNull(key)) getString(key) else null

    private fun JSONObject.longOrNull(key: String): Long? =
        if (has(key) && !isNull(key)) getLong(key) else null
}

class ReminderSyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = try {
        ReminderSync.syncNow(applicationContext)
        Result.success()
    } catch (e: IOException) {
        Log.w("ReminderSync", "sync failed, will retry: ${e.message}")
        Result.retry()
    }
}
