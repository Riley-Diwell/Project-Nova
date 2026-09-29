package com.example.novav2.knowledge

import android.content.Context
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.example.novav2.auth.AuthRepository
import com.example.novav2.network.NovaApiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONException
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Learns from recent activity - consolidation - without anyone pressing a button.
 *
 * WHY THE PHONE RUNS IT
 * The server decides WHEN a run is due (enough new episodes, or a day since the last one) and
 * says so on /event replies and graph fetches. But Cloud Run stops giving a request CPU once its
 * response is sent, so a run the server started "in the background" might never finish. A
 * request the phone makes keeps its CPU for as long as the run takes - so the phone does the
 * asking, from here, and the server's lease makes overlapping asks harmless.
 *
 * Two ways in:
 *  - [runSoon]: the server said a run is due. Once, as soon as there's a network.
 *  - [schedulePeriodic]: a daily backstop while charging on Wi-Fi, for a user who hasn't
 *    opened the map or spoken to NOVA in a while.
 * Either way the server only does work that's due, and only over episodes it hasn't read.
 */
class ConsolidationWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        if (!AuthRepository.isSignedIn) return Result.success()
        withContext(Dispatchers.Main) { KnowledgeRepository.onLearning(true) }
        return try {
            val run = NovaApiClient.consolidateIfDue()
            withContext(Dispatchers.Main) { KnowledgeRepository.onConsolidated(run) }
            Result.success()
        } catch (e: IOException) {
            Log.w(TAG, "consolidation failed, will retry: $e")
            if (runAttemptCount < MAX_ATTEMPTS) Result.retry() else Result.failure()
        } catch (e: JSONException) {
            Log.w(TAG, "consolidation reply unreadable: $e")
            Result.failure()
        } finally {
            withContext(Dispatchers.Main) { KnowledgeRepository.onLearning(false) }
        }
    }

    companion object {
        private const val TAG = "ConsolidationWorker"
        private const val SOON = "consolidation-soon"
        private const val DAILY = "consolidation-daily"
        private const val MAX_ATTEMPTS = 3

        /** The server said a run is due. KEEP: one already queued or running covers it. */
        fun runSoon(context: Context) {
            val request = OneTimeWorkRequestBuilder<ConsolidationWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
                .build()
            WorkManager.getInstance(context.applicationContext)
                .enqueueUniqueWork(SOON, ExistingWorkPolicy.KEEP, request)
        }

        /** The daily backstop. Charging and unmetered: a run can take a minute of model calls. */
        fun schedulePeriodic(context: Context) {
            val request = PeriodicWorkRequestBuilder<ConsolidationWorker>(24, TimeUnit.HOURS)
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.UNMETERED)
                        .setRequiresCharging(true)
                        .build()
                )
                .build()
            WorkManager.getInstance(context.applicationContext)
                .enqueueUniquePeriodicWork(DAILY, ExistingPeriodicWorkPolicy.KEEP, request)
        }

        /** Sign-out: nothing of this account's should run after it's gone. */
        fun cancel(context: Context) {
            val work = WorkManager.getInstance(context.applicationContext)
            work.cancelUniqueWork(SOON)
            work.cancelUniqueWork(DAILY)
        }
    }
}
