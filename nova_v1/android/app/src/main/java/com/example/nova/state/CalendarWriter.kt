package com.example.nova.state

import android.Manifest
import android.accounts.Account
import android.content.ContentResolver
import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.CalendarContract
import androidx.core.content.ContextCompat
import java.util.TimeZone

/** Writes events into the device calendar (DESIGN.md §5.3 actions[] -> calendar.create_event). */
object CalendarWriter {

    private data class WritableCalendar(val id: Long, val accountName: String, val accountType: String)

    fun hasPermission(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.WRITE_CALENDAR) ==
            PackageManager.PERMISSION_GRANTED

    /**
     * Inserts an event into the first writable calendar found, then requests an expedited sync
     * so a synced calendar (e.g. Google) doesn't wait on Android's default sync scheduling -
     * without it, the local insert can take minutes to reach the account's server / other devices.
     * Returns the event's content Uri, or null on failure.
     *
     * [rrule] is an RFC 5545 recurrence rule (e.g. "FREQ=WEEKLY;INTERVAL=2;COUNT=5"), built by
     * NovaApiClient from add_calendar_event's structured recurrence input - never null unless the
     * user asked for a one-off event. The Calendar Provider rejects an insert that sets both
     * DTEND and RRULE, so a recurring event carries its length as DURATION instead; see
     * https://developer.android.com/guide/topics/providers/calendar-provider#recurring-events.
     */
    fun createEvent(
        context: Context,
        title: String,
        startMillis: Long,
        endMillis: Long,
        description: String? = null,
        rrule: String? = null,
    ): Uri? {
        if (!hasPermission(context)) return null
        val calendar = writableCalendar(context) ?: return null

        val values = ContentValues().apply {
            put(CalendarContract.Events.CALENDAR_ID, calendar.id)
            put(CalendarContract.Events.TITLE, title)
            put(CalendarContract.Events.DESCRIPTION, description)
            put(CalendarContract.Events.DTSTART, startMillis)
            put(CalendarContract.Events.EVENT_TIMEZONE, TimeZone.getDefault().id)
            if (rrule.isNullOrBlank()) {
                put(CalendarContract.Events.DTEND, endMillis)
            } else {
                put(CalendarContract.Events.RRULE, rrule)
                put(CalendarContract.Events.DURATION, "PT${(endMillis - startMillis) / 1000}S")
            }
        }
        val uri = context.contentResolver.insert(CalendarContract.Events.CONTENT_URI, values)

        if (uri != null && calendar.accountType != CalendarContract.ACCOUNT_TYPE_LOCAL) {
            val syncBundle = Bundle().apply {
                putBoolean(ContentResolver.SYNC_EXTRAS_EXPEDITED, true)
                putBoolean(ContentResolver.SYNC_EXTRAS_MANUAL, true)
            }
            ContentResolver.requestSync(
                Account(calendar.accountName, calendar.accountType),
                CalendarContract.AUTHORITY,
                syncBundle
            )
        }

        return uri
    }

    /**
     * Changes one event by its CalendarContract.Events._ID (edit_calendar_event's event_id).
     * Every parameter but [eventId] is "only if it's changing" - anything left null keeps its
     * current value, which is why this reads the existing row first rather than writing a bare
     * partial ContentValues: DTEND and RRULE/DURATION are mutually exclusive on the Calendar
     * Provider (see createEvent), so touching just start/end on an already-recurring event
     * without knowing its current RRULE/DURATION risks writing DTEND alongside a still-present
     * RRULE and corrupting the row. Reading first sidesteps that regardless of which fields the
     * caller actually supplied.
     */
    fun updateEvent(
        context: Context,
        eventId: Long,
        title: String? = null,
        startMillis: Long? = null,
        endMillis: Long? = null,
        description: String? = null,
        rrule: String? = null,
    ): Boolean {
        if (!hasPermission(context)) return false
        val existing = queryEvent(context, eventId) ?: return false

        val finalStart = startMillis ?: existing.dtStart
        val finalEnd = endMillis
            ?: existing.dtEnd
            ?: existing.duration?.let { parseDurationMillis(it) }?.let { existing.dtStart + it }
            ?: finalStart
        val finalRrule = rrule ?: existing.rrule

        val values = ContentValues().apply {
            put(CalendarContract.Events.TITLE, title ?: existing.title)
            put(CalendarContract.Events.DESCRIPTION, description ?: existing.description)
            put(CalendarContract.Events.DTSTART, finalStart)
            if (finalRrule.isNullOrBlank()) {
                putNull(CalendarContract.Events.RRULE)
                putNull(CalendarContract.Events.DURATION)
                put(CalendarContract.Events.DTEND, finalEnd)
            } else {
                putNull(CalendarContract.Events.DTEND)
                put(CalendarContract.Events.RRULE, finalRrule)
                put(CalendarContract.Events.DURATION, "PT${(finalEnd - finalStart) / 1000}S")
            }
        }
        val uri = ContentUris.withAppendedId(CalendarContract.Events.CONTENT_URI, eventId)
        return context.contentResolver.update(uri, values, null, null) > 0
    }

    private data class ExistingEvent(
        val title: String?,
        val description: String?,
        val dtStart: Long,
        val dtEnd: Long?,
        val duration: String?,
        val rrule: String?,
    )

    private fun queryEvent(context: Context, eventId: Long): ExistingEvent? {
        val uri = ContentUris.withAppendedId(CalendarContract.Events.CONTENT_URI, eventId)
        val projection = arrayOf(
            CalendarContract.Events.TITLE,
            CalendarContract.Events.DESCRIPTION,
            CalendarContract.Events.DTSTART,
            CalendarContract.Events.DTEND,
            CalendarContract.Events.DURATION,
            CalendarContract.Events.RRULE,
        )
        return context.contentResolver.query(uri, projection, null, null, null)?.use { cursor ->
            if (!cursor.moveToFirst()) return@use null
            ExistingEvent(
                title = cursor.getString(0),
                description = cursor.getString(1),
                dtStart = cursor.getLong(2),
                dtEnd = if (cursor.isNull(3)) null else cursor.getLong(3),
                duration = cursor.getString(4),
                rrule = cursor.getString(5),
            )
        }
    }

    /**
     * RFC 5545 duration (e.g. "PT3600S", the form createEvent writes, or the "P1DT2H" style
     * other calendar apps may have written) to millis. Null on anything that doesn't parse -
     * this is an existing row's own field, not LLM input, but a duration this narrow reader
     * can't make sense of is still safer to treat as absent than to guess at.
     */
    private fun parseDurationMillis(duration: String): Long? {
        val match = Regex("^P(?:(\\d+)W)?(?:(\\d+)D)?(?:T(?:(\\d+)H)?(?:(\\d+)M)?(?:(\\d+)S)?)?$")
            .matchEntire(duration) ?: return null
        val (weeks, days, hours, minutes, seconds) = match.destructured
        val totalSeconds = (weeks.toLongOrNull() ?: 0L) * 7 * 86_400L +
            (days.toLongOrNull() ?: 0L) * 86_400L +
            (hours.toLongOrNull() ?: 0L) * 3_600L +
            (minutes.toLongOrNull() ?: 0L) * 60L +
            (seconds.toLongOrNull() ?: 0L)
        return totalSeconds * 1000L
    }

    /**
     * Deletes one event by its CalendarContract.Events._ID (delete_calendar_event's event_id).
     * Deletes the whole series for a recurring event's id, not a single occurrence - the model
     * only ever sees whole events via get_calendar_range, so it can't target one instance anyway.
     *
     * Callers MUST have already gotten explicit user confirmation (e.g. a Yes/No dialog) before
     * calling this - unlike createEvent, this is destructive and not something NOVA gets to
     * decide alone regardless of gain. See DeleteCalendarEventTool's docstring on the backend.
     */
    fun deleteEvent(context: Context, eventId: Long): Boolean {
        if (!hasPermission(context)) return false
        val uri = ContentUris.withAppendedId(CalendarContract.Events.CONTENT_URI, eventId)
        return context.contentResolver.delete(uri, null, null) > 0
    }

    /** First calendar the user can write to (CAL_ACCESS_CONTRIBUTOR or higher). Guesses on multi-account devices. */
    private fun writableCalendar(context: Context): WritableCalendar? {
        val projection = arrayOf(
            CalendarContract.Calendars._ID,
            CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL,
            CalendarContract.Calendars.ACCOUNT_NAME,
            CalendarContract.Calendars.ACCOUNT_TYPE
        )
        val selection = "${CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL} >= ?"
        val selectionArgs = arrayOf(CalendarContract.Calendars.CAL_ACCESS_CONTRIBUTOR.toString())

        return context.contentResolver.query(
            CalendarContract.Calendars.CONTENT_URI,
            projection,
            selection,
            selectionArgs,
            null
        )?.use { cursor ->
            if (cursor.moveToFirst()) {
                WritableCalendar(
                    id = cursor.getLong(0),
                    accountName = cursor.getString(2),
                    accountType = cursor.getString(3)
                )
            } else {
                null
            }
        }
    }
}
