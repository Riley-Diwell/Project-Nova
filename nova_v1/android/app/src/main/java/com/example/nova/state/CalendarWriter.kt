package com.example.nova.state

import android.Manifest
import android.accounts.Account
import android.content.ContentResolver
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
     */
    fun createEvent(
        context: Context,
        title: String,
        startMillis: Long,
        endMillis: Long,
        description: String? = null
    ): Uri? {
        if (!hasPermission(context)) return null
        val calendar = writableCalendar(context) ?: return null

        val values = ContentValues().apply {
            put(CalendarContract.Events.CALENDAR_ID, calendar.id)
            put(CalendarContract.Events.TITLE, title)
            put(CalendarContract.Events.DESCRIPTION, description)
            put(CalendarContract.Events.DTSTART, startMillis)
            put(CalendarContract.Events.DTEND, endMillis)
            put(CalendarContract.Events.EVENT_TIMEZONE, TimeZone.getDefault().id)
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
