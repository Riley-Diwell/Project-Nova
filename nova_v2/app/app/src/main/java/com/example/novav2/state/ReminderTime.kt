package com.example.novav2.state

import com.example.novav2.model.ReminderRecurrence
import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException

/**
 * Reminder time arithmetic, kept pure (a zone is always passed in) so it can be tested in
 * Australia/Sydney across the 4 Oct daylight-saving gap without touching the device clock.
 *
 * The rule throughout: a reminder's time is a floating LOCAL wall clock ([ReminderEntity.dueLocal]
 * [com.example.novav2.data.ReminderEntity.dueLocal]), resolved to an instant only when an alarm
 * needs one. Recurrence advances in local time (plusDays/plusWeeks), never by adding 24h of
 * milliseconds, so "every Monday 9am" stays 9am across a DST switch.
 */
object ReminderTime {
    private val LOCAL_FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss")

    fun format(local: LocalDateTime): String = local.withNano(0).format(LOCAL_FORMAT)

    /** A local wall clock from the wire. Accepts an offset form too (converted into [zone]) even
     * though the backend is told never to send one - LLM-produced input, not a validated
     * contract. Null if it parses as neither. */
    fun parseLocal(value: String?, zone: ZoneId): LocalDateTime? {
        if (value.isNullOrBlank()) return null
        val trimmed = value.trim()
        return try {
            LocalDateTime.parse(trimmed)
        } catch (e: DateTimeParseException) {
            try {
                OffsetDateTime.parse(trimmed).atZoneSameInstant(zone).toLocalDateTime()
            } catch (e2: DateTimeParseException) {
                null
            }
        }
    }

    /** The instant [local] happens in [zone]. In a DST gap this moves forward by the gap
     * (02:30 on 4 Oct in Sydney is 03:30); in an overlap it takes the earlier instant. */
    fun toMillis(local: LocalDateTime, zone: ZoneId): Long =
        local.atZone(zone).toInstant().toEpochMilli()

    fun toMillis(dueLocal: String, zone: ZoneId): Long? =
        parseLocal(dueLocal, zone)?.let { toMillis(it, zone) }

    fun toLocal(millis: Long, zone: ZoneId): LocalDateTime =
        LocalDateTime.ofInstant(Instant.ofEpochMilli(millis), zone)

    /** One step of [recurrence] after [from], in local time. */
    fun step(from: LocalDateTime, recurrence: ReminderRecurrence): LocalDateTime {
        val n = recurrence.interval.coerceAtLeast(1).toLong()
        return when (recurrence.frequency) {
            "daily" -> from.plusDays(n)
            "weekly" -> from.plusWeeks(n)
            "monthly" -> from.plusMonths(n)
            else -> from.plusYears(n)
        }
    }

    /** The next occurrence of a recurring reminder that was due at [current], skipping any
     * that are already past [nowLocal]. Returns the new due time and the occurrences left
     * (including it), or null if the series is finished (count used up, or past its until). */
    fun nextOccurrence(
        current: LocalDateTime,
        recurrence: ReminderRecurrence,
        nowLocal: LocalDateTime,
    ): Pair<LocalDateTime, Int?>? {
        val until = recurrence.untilLocal?.let {
            try { LocalDateTime.parse(it) } catch (e: DateTimeParseException) { null }
        }
        var remaining = recurrence.count
        var next = current
        // Bounded so a malformed series can't spin: 2000 daily steps is over five years.
        repeat(2000) {
            next = step(next, recurrence)
            if (remaining != null) {
                remaining = remaining!! - 1
                if (remaining!! < 1) return null
            }
            if (until != null && next.isAfter(until)) return null
            if (next.isAfter(nowLocal)) return next to remaining
        }
        return null
    }

    /** The latest occurrence after [current] that is already due by [byLocal] - where a
     * recurring reminder should be when several went by unanswered (or the phone was off).
     * Null if even the first one after [current] isn't due yet, or the series ended first.
     * The count is occurrences left including the returned one, as in [nextOccurrence]. */
    fun latestOccurrenceBy(
        current: LocalDateTime,
        recurrence: ReminderRecurrence,
        byLocal: LocalDateTime,
    ): Pair<LocalDateTime, Int?>? {
        val until = recurrence.untilLocal?.let {
            try { LocalDateTime.parse(it) } catch (e: DateTimeParseException) { null }
        }
        var remaining = recurrence.count
        var at = current
        var latest: Pair<LocalDateTime, Int?>? = null
        repeat(2000) {
            val next = step(at, recurrence)
            val left = remaining?.let { it - 1 }
            if (left != null && left < 1) return latest
            if (until != null && next.isAfter(until)) return latest
            if (next.isAfter(byLocal)) return latest
            at = next
            remaining = left
            latest = next to left
        }
        return latest
    }

    /** "4:30pm" - lowercase, no leading zero, matching narration.py's _format_time. */
    fun clock(local: LocalDateTime): String {
        val hour12 = local.hour % 12
        val ampm = if (local.hour < 12) "am" else "pm"
        return "${if (hour12 == 0) 12 else hour12}:${"%02d".format(local.minute)}$ampm"
    }

    fun clock(millis: Long, zone: ZoneId): String = clock(toLocal(millis, zone))

    /** "4:30pm" today, "Tomorrow 9:00am", "Mon 5 Oct, 9:00am" - for the Reminders list. */
    fun describe(local: LocalDateTime, nowLocal: LocalDateTime): String {
        val days = java.time.temporal.ChronoUnit.DAYS.between(nowLocal.toLocalDate(), local.toLocalDate())
        return when (days) {
            0L -> clock(local)
            1L -> "Tomorrow ${clock(local)}"
            -1L -> "Yesterday ${clock(local)}"
            else -> "${local.format(DateTimeFormatter.ofPattern("EEE d MMM"))}, ${clock(local)}"
        }
    }
}
