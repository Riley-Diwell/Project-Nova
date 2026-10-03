package com.example.novav2.data

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Upsert
import kotlinx.coroutines.flow.Flow

/** Scheduled = waiting on an alarm. Kept as one literal so every query below agrees. */
private const val SCHEDULED = "('pending','snoozed','deferred')"

@Dao
interface ReminderDao {
    @Upsert
    suspend fun upsert(reminder: ReminderEntity)

    @Upsert
    suspend fun upsertAll(reminders: List<ReminderEntity>)

    @Query("SELECT * FROM reminders WHERE id = :id")
    suspend fun byId(id: String): ReminderEntity?

    /** Everything the Reminders screen can show. Re-emits on every write, from any writer. */
    @Query("SELECT * FROM reminders ORDER BY triggerAtMillis ASC")
    fun observeAll(): Flow<List<ReminderEntity>>

    @Query("SELECT * FROM reminders ORDER BY triggerAtMillis ASC")
    suspend fun all(): List<ReminderEntity>

    /** Waiting on an alarm and due by [atMillis] - what an alarm delivers. */
    @Query("SELECT * FROM reminders WHERE status IN $SCHEDULED AND triggerAtMillis <= :atMillis ORDER BY triggerAtMillis ASC")
    suspend fun scheduledDueBy(atMillis: Long): List<ReminderEntity>

    /** Went off, still unanswered, and due its one re-buzz by [atMillis]. */
    @Query("SELECT * FROM reminders WHERE status = 'fired' AND rebuzzAtMillis IS NOT NULL AND rebuzzAtMillis <= :atMillis")
    suspend fun rebuzzDueBy(atMillis: Long): List<ReminderEntity>

    /** Went off unanswered, and its next occurrence is due by [atMillis] (a fired recurring
     * reminder's triggerAtMillis is its next occurrence - ReminderTransitions.fire). */
    @Query("SELECT * FROM reminders WHERE status = 'fired' AND recurFrequency IS NOT NULL AND triggerAtMillis <= :atMillis")
    suspend fun recurringFiredDueBy(atMillis: Long): List<ReminderEntity>

    /** The next instant anything needs the alarm - one exact alarm is set for this. */
    @Query(
        "SELECT MIN(t) FROM (" +
            "SELECT triggerAtMillis AS t FROM reminders WHERE status IN $SCHEDULED " +
            "UNION ALL " +
            "SELECT rebuzzAtMillis AS t FROM reminders WHERE status = 'fired' AND rebuzzAtMillis IS NOT NULL " +
            "UNION ALL " +
            "SELECT triggerAtMillis AS t FROM reminders WHERE status = 'fired' AND recurFrequency IS NOT NULL" +
            ")"
    )
    suspend fun nextTrigger(): Long?

    /** Not finished with: scheduled, or fired and unanswered. */
    @Query("SELECT * FROM reminders WHERE status IN ('pending','snoozed','deferred','fired') ORDER BY triggerAtMillis ASC")
    suspend fun active(): List<ReminderEntity>

    @Query("SELECT * FROM reminders WHERE status = 'pending'")
    suspend fun pending(): List<ReminderEntity>

    /** Place reminders a geofence should be watching for: waiting on their place, or gone off
     * and set to go off again every time (state/GeofenceRegistrar.kt). */
    @Query("SELECT * FROM reminders WHERE placeOn IS NOT NULL AND (status = 'pending' OR (everyTime = 1 AND status = 'fired'))")
    suspend fun armedAtPlaces(): List<ReminderEntity>

    /** Floating local times a zone change has to re-resolve: pending ones, and the next
     * occurrence of fired recurring ones. */
    @Query("SELECT * FROM reminders WHERE status = 'pending' OR (status = 'fired' AND recurFrequency IS NOT NULL)")
    suspend fun floating(): List<ReminderEntity>

    /** Changed on this phone and not yet uploaded (state/ReminderSync.kt). */
    @Query("SELECT * FROM reminders WHERE dirty = 1")
    suspend fun dirty(): List<ReminderEntity>

    @Query("SELECT COUNT(*) FROM reminders WHERE dirty = 1")
    suspend fun dirtyCount(): Int

    /** Uploaded - unless it changed again while the upload was in flight. */
    @Query("UPDATE reminders SET dirty = 0 WHERE id = :id AND updatedAtMillis = :updatedAtMillis")
    suspend fun markClean(id: String, updatedAtMillis: Long)

    /** First sign-in on an install that already had reminders: upload them all. */
    @Query("UPDATE reminders SET dirty = 1")
    suspend fun markAllDirty()

    /** Sign-out: the account keeps them; this phone forgets them. */
    @Query("DELETE FROM reminders")
    suspend fun deleteAll()

    /** Deleted or finished long enough ago to forget (30 days). */
    @Query("DELETE FROM reminders WHERE status IN ('done','cancelled') AND updatedAtMillis < :beforeMillis")
    suspend fun purgeFinishedBefore(beforeMillis: Long)
}
