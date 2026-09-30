package com.example.novav2.state

import com.example.novav2.data.ReminderEntity
import com.example.novav2.model.ReminderOrigin
import com.example.novav2.model.ReminderPriority
import com.example.novav2.model.ReminderRecurrence
import com.example.novav2.model.ReminderStatus
import java.time.LocalDateTime
import java.time.ZoneId
import java.util.UUID

/**
 * The reminder state machine as pure functions from one row
 * to the next. [ReminderRepository] is the only caller: it loads a row, applies one of these,
 * writes the result and reconciles the alarm. Kept separate so the transitions are testable
 * without Room or a Context.
 */
object ReminderTransitions {

    fun create(
        text: String,
        due: LocalDateTime,
        zone: ZoneId,
        nowMillis: Long,
        priority: ReminderPriority = ReminderPriority.NORMAL,
        origin: ReminderOrigin = ReminderOrigin.REQUESTED,
        sourceEpisodeId: String? = null,
        recurrence: ReminderRecurrence? = null,
        id: String = UUID.randomUUID().toString(),
    ): ReminderEntity = ReminderEntity(
        id = id,
        text = text.trim(),
        dueLocal = ReminderTime.format(due),
        triggerAtMillis = ReminderTime.toMillis(due, zone),
        status = ReminderStatus.PENDING.wire,
        priority = priority.wire,
        origin = origin.wire,
        sourceEpisodeId = sourceEpisodeId,
        recurFrequency = recurrence?.frequency,
        recurInterval = recurrence?.interval ?: 1,
        recurCountRemaining = recurrence?.count,
        recurUntilLocal = recurrence?.untilLocal,
        createdAtMillis = nowMillis,
        updatedAtMillis = nowMillis,
    )

    /** Delivered. An important one gets [rebuzzAtMillis] for its one follow-up buzz.
     *
     * A recurring one also arms its next occurrence: [ReminderEntity.dueLocal] stays the time
     * that just went off (what the list and the model see), and triggerAtMillis moves to the
     * next one, so ignoring today's doesn't swallow tomorrow's ([rollForward] takes it there). */
    fun fire(
        r: ReminderEntity,
        nowMillis: Long,
        rebuzzAtMillis: Long?,
        zone: ZoneId = ZoneId.systemDefault(),
    ): ReminderEntity = armNext(
        r.copy(
            status = ReminderStatus.FIRED.wire,
            firedAtMillis = nowMillis,
            rebuzzAtMillis = rebuzzAtMillis,
            updatedAtMillis = nowMillis,
        ),
        nowMillis,
        zone,
    )

    /** A fired recurring reminder's triggerAtMillis set to its next occurrence after now. If the
     * series has ended it stops being recurring, so it rests as an ordinary fired reminder. */
    fun armNext(r: ReminderEntity, nowMillis: Long, zone: ZoneId): ReminderEntity {
        val recurrence = r.recurrence ?: return r
        val current = ReminderTime.parseLocal(r.dueLocal, zone) ?: return r
        val next = ReminderTime.nextOccurrence(current, recurrence, ReminderTime.toLocal(nowMillis, zone))
            ?: return r.copy(recurFrequency = null, recurInterval = 1, recurCountRemaining = null, recurUntilLocal = null)
        return r.copy(triggerAtMillis = ReminderTime.toMillis(next.first, zone))
    }

    /** A recurring reminder moved on to the latest of its occurrences already due - the next
     * one arriving while the last went unanswered, or several missed while the phone was off.
     * Pending at that occurrence, ready to deliver; null if no later occurrence is due yet. */
    fun rollForward(r: ReminderEntity, nowMillis: Long, zone: ZoneId): ReminderEntity? {
        val recurrence = r.recurrence ?: return null
        val current = ReminderTime.parseLocal(r.dueLocal, zone) ?: return null
        val latest = ReminderTime.latestOccurrenceBy(current, recurrence, ReminderTime.toLocal(nowMillis, zone))
            ?: return null
        return reset(r, latest.first, zone, nowMillis).copy(recurCountRemaining = latest.second)
    }

    /** The follow-up buzz happened - there is only ever one. */
    fun rebuzzed(r: ReminderEntity, nowMillis: Long): ReminderEntity =
        r.copy(rebuzzAtMillis = null, updatedAtMillis = nowMillis)

    fun defer(r: ReminderEntity, untilMillis: Long, reason: String, nowMillis: Long): ReminderEntity = r.copy(
        status = ReminderStatus.DEFERRED.wire,
        triggerAtMillis = untilMillis,
        deferReason = reason,
        deferCount = r.deferCount + 1,
        firstDeferredAtMillis = r.firstDeferredAtMillis ?: nowMillis,
        updatedAtMillis = nowMillis,
    )

    fun snooze(r: ReminderEntity, minutes: Int, nowMillis: Long): ReminderEntity = r.copy(
        status = ReminderStatus.SNOOZED.wire,
        triggerAtMillis = nowMillis + minutes.coerceAtLeast(1) * 60_000L,
        snoozeCount = r.snoozeCount + 1,
        rebuzzAtMillis = null,
        updatedAtMillis = nowMillis,
    )

    /** Done - or, for a recurring reminder, straight on to its next occurrence (computed in
     * local time, skipping any already past) as the same row. */
    fun complete(r: ReminderEntity, nowMillis: Long, zone: ZoneId): ReminderEntity {
        val recurrence = r.recurrence
        val current = ReminderTime.parseLocal(r.dueLocal, zone)
        if (recurrence != null && current != null) {
            val next = ReminderTime.nextOccurrence(current, recurrence, ReminderTime.toLocal(nowMillis, zone))
            if (next != null) {
                return reset(r, next.first, zone, nowMillis).copy(
                    recurCountRemaining = next.second,
                    completedAtMillis = nowMillis,
                )
            }
        }
        return r.copy(
            status = ReminderStatus.DONE.wire,
            completedAtMillis = nowMillis,
            rebuzzAtMillis = null,
            updatedAtMillis = nowMillis,
        )
    }

    /** Swiped off the Done list. Only a done reminder can be cleared - anything else still has
     * something to say. */
    fun clear(r: ReminderEntity, nowMillis: Long): ReminderEntity? =
        if (r.statusEnum != ReminderStatus.DONE || r.clearedAtMillis != null) null
        else r.copy(clearedAtMillis = nowMillis, updatedAtMillis = nowMillis)

    fun unclear(r: ReminderEntity, nowMillis: Long): ReminderEntity? =
        if (r.clearedAtMillis == null) null else r.copy(clearedAtMillis = null, updatedAtMillis = nowMillis)

    /** Soft delete - [undoCancel] restores whatever it was. */
    fun cancel(r: ReminderEntity, nowMillis: Long): ReminderEntity = r.copy(
        status = ReminderStatus.CANCELLED.wire,
        previousStatus = r.status,
        rebuzzAtMillis = null,
        updatedAtMillis = nowMillis,
    )

    fun undoCancel(r: ReminderEntity, nowMillis: Long): ReminderEntity {
        if (r.statusEnum != ReminderStatus.CANCELLED) return r
        return r.copy(
            status = r.previousStatus ?: ReminderStatus.PENDING.wire,
            previousStatus = null,
            updatedAtMillis = nowMillis,
        )
    }

    /** A new text, time or repeat. Any change of time makes it pending again at that time. */
    fun edit(
        r: ReminderEntity,
        nowMillis: Long,
        zone: ZoneId,
        text: String? = null,
        due: LocalDateTime? = null,
        priority: ReminderPriority? = null,
        recurrence: ReminderRecurrence? = null,
        clearRecurrence: Boolean = false,
    ): ReminderEntity {
        var next = r.copy(
            text = text?.trim()?.takeIf { it.isNotEmpty() } ?: r.text,
            priority = priority?.wire ?: r.priority,
            updatedAtMillis = nowMillis,
        )
        if (recurrence != null || clearRecurrence) {
            next = next.copy(
                recurFrequency = recurrence?.frequency,
                recurInterval = recurrence?.interval ?: 1,
                recurCountRemaining = recurrence?.count,
                recurUntilLocal = recurrence?.untilLocal,
            )
        }
        return if (due != null) reset(next, due, zone, nowMillis) else next
    }

    /** Re-resolve a pending reminder's floating time after a time or zone change, and a fired
     * recurring one's next occurrence. Anything snoozed or deferred holds an absolute instant
     * and is left alone. */
    fun rezone(r: ReminderEntity, zone: ZoneId, nowMillis: Long = System.currentTimeMillis()): ReminderEntity {
        if (r.statusEnum == ReminderStatus.FIRED && r.recurrence != null) return armNext(r, nowMillis, zone)
        if (r.statusEnum != ReminderStatus.PENDING) return r
        val millis = ReminderTime.toMillis(r.dueLocal, zone) ?: return r
        return if (millis == r.triggerAtMillis) r else r.copy(triggerAtMillis = millis)
    }

    /** Where the user wants an edit to land: a new local time, minutes from now, or the
     * existing time moved by [shiftMinutes]. Null if none was given or it didn't parse. */
    fun resolveEditDue(
        r: ReminderEntity,
        zone: ZoneId,
        nowMillis: Long,
        dueLocal: String?,
        inMinutes: Int?,
        shiftMinutes: Int?,
    ): LocalDateTime? = when {
        dueLocal != null -> ReminderTime.parseLocal(dueLocal, zone)
        inMinutes != null -> ReminderTime.toLocal(nowMillis, zone).plusMinutes(inMinutes.toLong())
        shiftMinutes != null -> ReminderTime.parseLocal(r.dueLocal, zone)?.plusMinutes(shiftMinutes.toLong())
        else -> null
    }

    private fun reset(r: ReminderEntity, due: LocalDateTime, zone: ZoneId, nowMillis: Long) = r.copy(
        status = ReminderStatus.PENDING.wire,
        dueLocal = ReminderTime.format(due),
        triggerAtMillis = ReminderTime.toMillis(due, zone),
        deferReason = null,
        deferCount = 0,
        firstDeferredAtMillis = null,
        snoozeCount = 0,
        rebuzzAtMillis = null,
        firedAtMillis = null,
        clearedAtMillis = null,
        updatedAtMillis = nowMillis,
    )
}
