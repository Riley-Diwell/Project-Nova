package com.example.novav2.data

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import com.example.novav2.model.ReminderOrigin
import com.example.novav2.model.ReminderPriority
import com.example.novav2.model.ReminderRecurrence
import com.example.novav2.model.ReminderStatus

/**
 * One reminder. The phone owns reminders day to day (alarms, offline), and
 * the signed-in account keeps a synced copy on the server so they survive sign-out and a new
 * phone ([com.example.novav2.state.ReminderSync]). Written only through
 * [com.example.novav2.state.ReminderRepository].
 *
 * Time has two fields on purpose. [dueLocal] is the reminder's time as the user meant it - a
 * floating local wall clock, "9am wherever I am" - and is the source of truth. [triggerAtMillis]
 * is the next instant to wake up at: [dueLocal] resolved in the current zone, or an absolute
 * snooze/defer instant. It is recomputed from [dueLocal] on boot and on time/zone changes, so a
 * daylight-saving switch or a flight never moves "Monday 9am".
 */
@Entity(tableName = "reminders", indices = [Index("status"), Index("triggerAtMillis")])
data class ReminderEntity(
    @PrimaryKey val id: String,
    val text: String,
    /** "2026-10-05T09:00:00" - floating local wall clock. */
    val dueLocal: String,
    val triggerAtMillis: Long,
    /** [ReminderStatus.wire]. */
    val status: String,
    /** [ReminderPriority.wire]. */
    val priority: String = ReminderPriority.NORMAL.wire,
    /** [ReminderOrigin.wire]. */
    val origin: String,
    /** The Episode whose Action created this - for POST /event/outcome when an inferred
     * reminder is finished (accepted) or deleted (rejected). */
    val sourceEpisodeId: String? = null,
    /** Set once that Outcome has been posted, so a recurring reminder reports it only once. */
    val outcomeReported: Boolean = false,
    /** Why it is being held ("COMP2100 Lecture", "call") - shown in the UI. */
    val deferReason: String? = null,
    val deferCount: Int = 0,
    val firstDeferredAtMillis: Long? = null,
    val snoozeCount: Int = 0,
    /** Status before a delete, so Undo can put it back. */
    val previousStatus: String? = null,
    /** An important reminder buzzes once more if it is still unanswered by then. */
    val rebuzzAtMillis: Long? = null,
    val recurFrequency: String? = null,
    val recurInterval: Int = 1,
    /** Occurrences left including the current one, or null for no limit. */
    val recurCountRemaining: Int? = null,
    val recurUntilLocal: String? = null,
    val createdAtMillis: Long,
    val updatedAtMillis: Long,
    val firedAtMillis: Long? = null,
    val completedAtMillis: Long? = null,
    /** A done reminder swiped away on the Reminders screen - hidden from the Done list, but kept
     * (and synced) so get_reminders can still answer "what did I finish yesterday". Purged with
     * the rest of the done rows after 30 days. */
    val clearedAtMillis: Long? = null,
    /** Changed here and not yet uploaded to the account. Local bookkeeping only - never synced. */
    @ColumnInfo(defaultValue = "0") val dirty: Boolean = false,
) {
    val statusEnum: ReminderStatus get() = ReminderStatus.fromWire(status)
    val priorityEnum: ReminderPriority get() = ReminderPriority.fromWire(priority)
    val originEnum: ReminderOrigin get() = ReminderOrigin.fromWire(origin)

    val recurrence: ReminderRecurrence?
        get() = recurFrequency?.let {
            ReminderRecurrence(it, recurInterval, recurCountRemaining, recurUntilLocal)
        }
}
