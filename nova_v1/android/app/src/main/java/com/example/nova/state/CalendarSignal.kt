package com.example.nova.state

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.provider.CalendarContract
import androidx.core.content.ContextCompat
import com.example.nova.model.CalendarEventInfo

data class CalendarSnapshot(
    val calendarCtx: String,                    // frozen-seam string: "free" / "in_event" / "busy_soon"
    val currentEvents: List<CalendarEventInfo>, // all in-progress events, sorted by priority desc
    val upcomingEvents: List<CalendarEventInfo>,// all events starting within UPCOMING_WINDOW_MILLIS, sorted by start time
)

/**
 * Reads calendar state from CalendarContract.Instances. One call to [snapshot] runs a single
 * query that covers both the current moment and the next [UPCOMING_WINDOW_MILLIS], then derives
 * the frozen-seam string and the two rich event objects from those results.
 *
 * Priority is: busy > tentative > free, then accepted > none/tentative, then timed > all-day.
 */
object CalendarSignal {

    private const val UPCOMING_WINDOW_MILLIS = 2 * 60 * 60 * 1000L  // 2 hours
    private const val BUSY_SOON_THRESHOLD_MILLIS = 30 * 60 * 1000L   // 30 minutes

    private val PROJECTION = arrayOf(
        CalendarContract.Instances.TITLE,
        CalendarContract.Instances.DTSTART,
        CalendarContract.Instances.DTEND,
        CalendarContract.Instances.EVENT_LOCATION,
        CalendarContract.Instances.AVAILABILITY,
        CalendarContract.Instances.ALL_DAY,
        CalendarContract.Instances.SELF_ATTENDEE_STATUS,
    )

    fun hasPermission(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CALENDAR) ==
            PackageManager.PERMISSION_GRANTED

    /**
     * Single-query snapshot covering now and the next 2 hours. Returns null if permission is
     * missing. The [CalendarSnapshot.calendarCtx] string is the value that goes into the frozen
     * seam field on UserState; the two event objects carry the rich detail.
     */
    fun snapshot(context: Context): CalendarSnapshot? {
        if (!hasPermission(context)) return null
        val now = System.currentTimeMillis()

        // One query from (now - 1s) covers ongoing events; (now + 2h) covers upcoming ones.
        val events = queryInstances(context, from = now - 1_000L, to = now + UPCOMING_WINDOW_MILLIS, now = now)

        val currentEvents = events
            .filter { it.minutesUntilStart <= 0 }
            .sortedByDescending { it.priorityScore() }
        val upcomingEvents = events
            .filter { it.minutesUntilStart > 0 }
            .sortedBy { it.startMillis }

        val calendarCtx = when {
            currentEvents.isNotEmpty() -> "in_event"
            upcomingEvents.any { it.startMillis - now <= BUSY_SOON_THRESHOLD_MILLIS } -> "busy_soon"
            else -> "free"
        }

        return CalendarSnapshot(calendarCtx, currentEvents, upcomingEvents)
    }

    /** Thin wrapper so any code that only needs the frozen-seam string can still call this. */
    fun currentCalendarContext(context: Context): String? = snapshot(context)?.calendarCtx

    /**
     * On-demand query over an arbitrary [fromMillis, toMillis) range, for when the backend's
     * Intent Surface asks for calendar data outside the [snapshot] window (e.g. "today",
     * "next week") via the get_calendar_range client tool. Returns null if permission is
     * missing.
     */
    fun rangeSnapshot(context: Context, fromMillis: Long, toMillis: Long): List<CalendarEventInfo>? {
        if (!hasPermission(context)) return null
        return queryInstances(context, from = fromMillis, to = toMillis, now = System.currentTimeMillis())
    }

    private fun queryInstances(context: Context, from: Long, to: Long, now: Long): List<CalendarEventInfo> {
        val uri = CalendarContract.Instances.CONTENT_URI.buildUpon()
            .also { ContentUris.appendId(it, from); ContentUris.appendId(it, to) }
            .build()

        val results = mutableListOf<CalendarEventInfo>()

        context.contentResolver.query(uri, PROJECTION, null, null, null)?.use { cursor ->
            val iTitle = cursor.getColumnIndex(CalendarContract.Instances.TITLE)
            val iStart = cursor.getColumnIndex(CalendarContract.Instances.DTSTART)
            val iEnd = cursor.getColumnIndex(CalendarContract.Instances.DTEND)
            val iLocation = cursor.getColumnIndex(CalendarContract.Instances.EVENT_LOCATION)
            val iAvailability = cursor.getColumnIndex(CalendarContract.Instances.AVAILABILITY)
            val iAllDay = cursor.getColumnIndex(CalendarContract.Instances.ALL_DAY)
            val iSelfStatus = cursor.getColumnIndex(CalendarContract.Instances.SELF_ATTENDEE_STATUS)

            while (cursor.moveToNext()) {
                val start = cursor.getLong(iStart)
                val availability = when (cursor.getInt(iAvailability)) {
                    CalendarContract.Events.AVAILABILITY_FREE -> "free"
                    CalendarContract.Events.AVAILABILITY_TENTATIVE -> "tentative"
                    else -> "busy"
                }
                val selfStatus = when (cursor.getInt(iSelfStatus)) {
                    CalendarContract.Attendees.ATTENDEE_STATUS_ACCEPTED -> "accepted"
                    CalendarContract.Attendees.ATTENDEE_STATUS_DECLINED -> "declined"
                    CalendarContract.Attendees.ATTENDEE_STATUS_TENTATIVE -> "tentative"
                    else -> "none"
                }
                results += CalendarEventInfo(
                    title = cursor.getString(iTitle) ?: "(no title)",
                    startMillis = start,
                    endMillis = cursor.getLong(iEnd),
                    location = cursor.getString(iLocation)?.takeIf { it.isNotBlank() },
                    availability = availability,
                    isAllDay = cursor.getInt(iAllDay) != 0,
                    selfStatus = selfStatus,
                    minutesUntilStart = ((start - now) / 60_000L).toInt(),
                )
            }
        }
        return results
    }
}
