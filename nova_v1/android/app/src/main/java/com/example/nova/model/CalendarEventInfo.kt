package com.example.nova.model

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * A single calendar event instance with enough detail for the Intent Surface to reason about
 * priority and context. [availability] mirrors CalendarContract values: "busy" / "free" /
 * "tentative". [selfStatus] is the user's own RSVP: "accepted" / "declined" / "tentative" / "none".
 * [minutesUntilStart] is negative while the event is in progress.
 */
data class CalendarEventInfo(
    val title: String,
    val startMillis: Long,
    val endMillis: Long,
    val location: String?,
    val availability: String,
    val isAllDay: Boolean,
    val selfStatus: String,
    val minutesUntilStart: Int,
) {
    /**
     * [startMillis]/[endMillis] rendered in this device's timezone, ISO 8601 with offset
     * (e.g. "2026-07-29T09:30:00+10:00").
     *
     * Epoch millis are an absolute instant carrying no timezone, so the backend's Intent
     * Surface - which has no idea where the phone is - reads them as UTC and speaks a 09:30
     * stand-up as 23:30. These are the fields it quotes back to the user instead.
     */
    val startLocal: String get() = startMillis.toLocalIso()
    val endLocal: String get() = endMillis.toLocalIso()
    /**
     * Higher score = more blocking / important. Used to pick the top event when multiple overlap.
     *   busy + accepted + timed = 6 (e.g. a confirmed meeting)
     *   busy alone           = 3 (e.g. a block you created)
     *   free / all-day       = 0-1 (e.g. a reminder or holiday)
     */
    fun priorityScore(): Int {
        var score = 0
        if (availability == "busy") score += 3
        if (availability == "tentative") score += 1
        if (selfStatus == "accepted") score += 2
        if (!isAllDay) score += 1
        return score
    }

    fun displayTime(): String = when {
        minutesUntilStart <= 0 -> "in progress"
        minutesUntilStart < 60 -> "in ${minutesUntilStart}min"
        else -> "in ${minutesUntilStart / 60}h ${minutesUntilStart % 60}min"
    }
}

/** Epoch millis -> ISO 8601 local time with offset, e.g. "2026-07-29T09:30:00+10:00". */
internal fun Long.toLocalIso(): String =
    Instant.ofEpochMilli(this)
        .atZone(ZoneId.systemDefault())
        .format(DateTimeFormatter.ISO_OFFSET_DATE_TIME)
