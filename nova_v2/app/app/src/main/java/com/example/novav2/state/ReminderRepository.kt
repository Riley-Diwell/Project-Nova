package com.example.novav2.state

import android.content.Context
import android.util.Log
import com.example.novav2.data.NovaDatabase
import com.example.novav2.data.ReminderDao
import com.example.novav2.data.ReminderEntity
import com.example.novav2.model.ReminderOrigin
import com.example.novav2.model.ReminderPriority
import com.example.novav2.model.ReminderRecurrence
import com.example.novav2.model.ReminderStatus
import com.example.novav2.model.ReminderSummary
import com.example.novav2.model.UserState
import com.example.novav2.network.NovaApiClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.time.LocalDateTime
import java.time.ZoneId

/**
 * The only writer of the reminders table. Every mutation -
 * from a voice Action, the wearable button, a notification action or the Reminders screen - goes
 * through here, applies one [ReminderTransitions] step, clears any notification that is no longer
 * true, and calls [ReminderScheduler.reconcile], so the one alarm always matches the table.
 *
 * Also where an inferred reminder's lifecycle becomes a gain Outcome: finishing one Nova
 * suggested reports "accepted" against the Episode that created it, deleting one reports
 * "rejected". Requested reminders report nothing extra - the turn that set them was
 * already scored when its reply was heard out.
 */
object ReminderRepository {
    private const val TAG = "ReminderRepository"

    /** What voice turns carry - fired in the last 12h, all snoozed/deferred, pending
     * within 7 days, soonest first, at most 15. */
    private const val WINDOW_FIRED_MILLIS = 12 * 60 * 60_000L
    private const val WINDOW_PENDING_MILLIS = 7 * 24 * 60 * 60_000L
    private const val WINDOW_MAX = 15
    private const val PURGE_AFTER_MILLIS = 30L * 24 * 60 * 60_000L

    /** An Action's due time may be at most this far in the past when it arrives. */
    private const val PAST_TOLERANCE_MILLIS = 2 * 60_000L

    private val mutex = Mutex()
    private val outcomeScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private fun dao(context: Context): ReminderDao =
        NovaDatabase.getInstance(context.applicationContext).reminderDao()

    private fun zone(): ZoneId = ZoneId.systemDefault()

    fun observeAll(context: Context): Flow<List<ReminderEntity>> = dao(context).observeAll()

    suspend fun byId(context: Context, id: String): ReminderEntity? = dao(context).byId(id)

    /** Null (and nothing stored) if [due] is already more than 2 minutes past. */
    suspend fun create(
        context: Context,
        text: String,
        due: LocalDateTime,
        priority: ReminderPriority = ReminderPriority.NORMAL,
        origin: ReminderOrigin = ReminderOrigin.MANUAL,
        sourceEpisodeId: String? = null,
        recurrence: ReminderRecurrence? = null,
    ): ReminderEntity? {
        val now = System.currentTimeMillis()
        val reminder = ReminderTransitions.create(
            text, due, zone(), now, priority, origin, sourceEpisodeId, recurrence,
        )
        if (reminder.text.isBlank() || reminder.triggerAtMillis < now - PAST_TOLERANCE_MILLIS) {
            Log.w(TAG, "not creating a reminder due in the past: ${reminder.dueLocal}")
            return null
        }
        mutex.withLock { dao(context).upsert(reminder.copy(dirty = true)) }
        ReminderScheduler.reconcile(context)
        ReminderSync.schedule(context)
        return reminder
    }

    suspend fun complete(context: Context, id: String): ReminderEntity? =
        mutate(context, id) { r, now ->
            if (!r.statusEnum.isActive) null else ReminderTransitions.complete(r, now, zone())
        }?.also { reportOutcome(context, it, accepted = true) }

    suspend fun snooze(context: Context, id: String, minutes: Int? = null): ReminderEntity? {
        val m = minutes ?: ReminderPreferences.snoozeMinutes(context)
        return mutate(context, id) { r, now ->
            if (!r.statusEnum.isActive) null else ReminderTransitions.snooze(r, m, now)
        }
    }

    suspend fun edit(
        context: Context,
        id: String,
        text: String? = null,
        due: LocalDateTime? = null,
        priority: ReminderPriority? = null,
        recurrence: ReminderRecurrence? = null,
        clearRecurrence: Boolean = false,
    ): ReminderEntity? = mutate(context, id) { r, now ->
        if (r.statusEnum == ReminderStatus.CANCELLED) null
        else ReminderTransitions.edit(r, now, zone(), text, due, priority, recurrence, clearRecurrence)
    }

    /** update_reminder's edit, with its time given the three ways the tool allows. */
    suspend fun editFromAction(
        context: Context,
        id: String,
        text: String?,
        dueLocal: String?,
        inMinutes: Int?,
        shiftMinutes: Int?,
        recurrence: ReminderRecurrence?,
    ): ReminderEntity? = mutate(context, id) { r, now ->
        if (r.statusEnum == ReminderStatus.CANCELLED) return@mutate null
        val due = ReminderTransitions.resolveEditDue(r, zone(), now, dueLocal, inMinutes, shiftMinutes)
        ReminderTransitions.edit(r, now, zone(), text = text, due = due, recurrence = recurrence)
    }

    suspend fun cancel(context: Context, id: String): ReminderEntity? =
        mutate(context, id) { r, now ->
            if (r.statusEnum == ReminderStatus.CANCELLED) null else ReminderTransitions.cancel(r, now)
        }?.also { reportOutcome(context, it, accepted = false) }

    /** Hide a done reminder from the Done list (and on every synced phone). */
    suspend fun clearDone(context: Context, id: String): ReminderEntity? =
        mutate(context, id) { r, now -> ReminderTransitions.clear(r, now) }

    suspend fun unclearDone(context: Context, id: String): ReminderEntity? =
        mutate(context, id) { r, now -> ReminderTransitions.unclear(r, now) }

    suspend fun undoCancel(context: Context, id: String): ReminderEntity? =
        mutate(context, id) { r, now -> ReminderTransitions.undoCancel(r, now) }

    /** Writes rows the engine has already transitioned (fire / defer / rebuzzed) in one go. */
    suspend fun saveAll(context: Context, reminders: List<ReminderEntity>) {
        if (reminders.isEmpty()) return
        mutex.withLock { dao(context).upsertAll(reminders.map { it.copy(dirty = true) }) }
        ReminderScheduler.reconcile(context)
        ReminderSync.schedule(context)
    }

    /**
     * [ReminderSync]'s merge, under the same lock as every local edit. [uploaded] are the rows the
     * server just accepted - clean again unless edited meanwhile. [fromAccount] are the account's
     * copies to adopt: each replaces the local one unless the local one is a newer, unsent edit
     * (last writer wins, as on the server).
     */
    suspend fun mergeFromAccount(
        context: Context,
        uploaded: List<ReminderEntity>,
        fromAccount: List<ReminderEntity>,
    ) {
        if (uploaded.isEmpty() && fromAccount.isEmpty()) return
        val adopted = mutex.withLock {
            val dao = dao(context)
            uploaded.forEach { dao.markClean(it.id, it.updatedAtMillis) }
            fromAccount.filter { remote ->
                val local = dao.byId(remote.id)
                val localIsNewer = local != null && local.dirty && local.updatedAtMillis > remote.updatedAtMillis
                if (!localIsNewer) dao.upsert(remote.copy(dirty = false))
                !localIsNewer
            }
        }
        adopted.filter { !it.statusEnum.isActive || it.statusEnum.isScheduled }
            .forEach { ReminderNotifier.cancel(context, it.id) }
        // Pending times arrive resolved in the other phone's zone; re-resolve them here.
        rezoneAll(context)
    }

    /** Sign-out: forget every reminder on this phone (the account keeps them). */
    suspend fun clearLocal(context: Context) {
        mutex.withLock {
            val dao = dao(context)
            dao.all().forEach { ReminderNotifier.cancel(context, it.id) }
            dao.deleteAll()
        }
        ReminderScheduler.reconcile(context)
    }

    /** After a time or zone change: re-resolve every floating time, then reconcile. */
    suspend fun rezoneAll(context: Context) {
        mutex.withLock {
            val dao = dao(context)
            val now = System.currentTimeMillis()
            val changed = dao.floating().map { ReminderTransitions.rezone(it, zone(), now) to it }
                .filter { (after, before) -> after != before }
                .map { it.first }
            if (changed.isNotEmpty()) dao.upsertAll(changed)
            dao.purgeFinishedBefore(System.currentTimeMillis() - PURGE_AFTER_MILLIS)
        }
        ReminderScheduler.reconcile(context)
    }

    /** The bounded window a voice turn carries, and how many active reminders there are in all
     * (so the model knows when the window isn't the whole list). */
    suspend fun serverWindow(context: Context): Pair<List<ReminderSummary>, Int> {
        val now = System.currentTimeMillis()
        val active = dao(context).active()
        val window = active.filter {
            when (it.statusEnum) {
                ReminderStatus.FIRED -> (it.firedAtMillis ?: now) >= now - WINDOW_FIRED_MILLIS
                ReminderStatus.SNOOZED, ReminderStatus.DEFERRED -> true
                else -> it.triggerAtMillis <= now + WINDOW_PENDING_MILLIS
            }
        }.sortedBy { it.triggerAtMillis }.take(WINDOW_MAX)
        return window.map { it.toSummary(now) } to active.size
    }

    /** [state] with the reminder window attached - voice turns only. Ambient snapshots stay
     * without it, so they carry no reminder text and cost no Room read. */
    suspend fun attachWindow(context: Context, state: UserState): UserState = try {
        val (window, total) = serverWindow(context)
        state.copy(reminders = window, remindersPendingTotal = total)
    } catch (e: Exception) {
        Log.w(TAG, "reminder window unavailable: $e")
        state
    }

    /** get_reminders: reminders due between two local times, for the NeedMore hop. */
    suspend fun range(
        context: Context,
        fromLocal: LocalDateTime?,
        toLocal: LocalDateTime?,
        includeDone: Boolean,
    ): List<ReminderSummary> {
        val now = System.currentTimeMillis()
        return dao(context).all().filter { r ->
            val status = r.statusEnum
            if (status == ReminderStatus.CANCELLED) return@filter false
            if (status == ReminderStatus.DONE && !includeDone) return@filter false
            val due = ReminderTime.parseLocal(r.dueLocal, zone()) ?: return@filter false
            (fromLocal == null || !due.isBefore(fromLocal)) && (toLocal == null || due.isBefore(toLocal))
        }.sortedBy { it.dueLocal }.map { it.toSummary(now) }
    }

    private suspend fun mutate(
        context: Context,
        id: String,
        transition: (ReminderEntity, Long) -> ReminderEntity?,
    ): ReminderEntity? {
        val updated = mutex.withLock {
            val dao = dao(context)
            val current = dao.byId(id) ?: run {
                Log.w(TAG, "no reminder $id - ignoring")
                return@withLock null
            }
            val next = transition(current, System.currentTimeMillis()) ?: return@withLock null
            dao.upsert(next.copy(dirty = true))
            next
        } ?: return null
        if (!updated.statusEnum.isActive || updated.statusEnum.isScheduled) {
            ReminderNotifier.cancel(context, updated.id)
        }
        ReminderScheduler.reconcile(context)
        ReminderSync.schedule(context)
        return updated
    }

    private fun reportOutcome(context: Context, reminder: ReminderEntity, accepted: Boolean) {
        val episodeId = reminder.sourceEpisodeId ?: return
        if (reminder.originEnum != ReminderOrigin.INFERRED || reminder.outcomeReported) return
        val app = context.applicationContext
        outcomeScope.launch {
            NovaApiClient.postOutcome(episodeId, accepted)
            mutex.withLock {
                dao(app).byId(reminder.id)?.let { dao(app).upsert(it.copy(outcomeReported = true)) }
            }
        }
    }

    private fun ReminderEntity.dueMillis(): Long =
        if (statusEnum == ReminderStatus.FIRED) ReminderTime.toMillis(dueLocal, zone()) ?: triggerAtMillis
        else triggerAtMillis

    private fun ReminderEntity.toSummary(nowMillis: Long): ReminderSummary = ReminderSummary(
        id = id,
        text = text,
        dueLocal = dueLocal,
        // A fired recurring reminder's triggerAtMillis is already its next occurrence.
        minutesUntilDue = Math.floorDiv(dueMillis() - nowMillis, 60_000L).toInt(),
        status = status,
        priority = priority,
        firedMinutesAgo = if (statusEnum == ReminderStatus.FIRED) {
            firedAtMillis?.let { ((nowMillis - it) / 60_000L).toInt() }
        } else null,
        recurrence = recurrence?.describe(),
    )
}
