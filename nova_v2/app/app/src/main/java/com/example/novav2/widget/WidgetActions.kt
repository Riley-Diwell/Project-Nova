package com.example.novav2.widget

import android.content.ContentUris
import android.content.Context
import android.content.Intent
import android.provider.CalendarContract
import androidx.glance.GlanceId
import androidx.glance.action.ActionParameters
import androidx.glance.appwidget.action.ActionCallback
import com.example.novav2.AssistTrampolineActivity
import com.example.novav2.MainActivity
import com.example.novav2.state.ReminderRepository

/**
 * Ticking a reminder on the widget completes it. ReminderRepository.complete already runs
 * ReminderScheduler.reconcile, which redraws the widget, stops the device LED and queues the
 * server sync - so there is deliberately no second update path here.
 */
class CompleteReminderAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val id = parameters[REMINDER_ID] ?: return
        ReminderRepository.complete(context, id)
    }

    companion object {
        val REMINDER_ID = ActionParameters.Key<String>("reminder_id")
    }
}

/** The intents behind the widget's taps - one obvious thing each. */
object WidgetIntents {
    private const val EVENT_MIME_TYPE = "vnd.android.cursor.item/event"

    fun openApp(context: Context) = Intent(context, MainActivity::class.java)

    /** MainActivity already handles this (the reminder notification's tap uses it too). */
    fun openReminders(context: Context) =
        Intent(context, MainActivity::class.java).setAction(MainActivity.ACTION_OPEN_REMINDERS)

    /** The same entry point as the power-button assist gesture, so its "is Nova already open /
     * can it listen headlessly" decision is reused rather than copied. */
    fun talk(context: Context) = Intent(context, AssistTrampolineActivity::class.java)

    /** The calendar app at that event, or Nova if no app on the phone opens calendar events
     * (the manifest's <queries> entry is what lets this check see them on API 30+). */
    fun openEvent(context: Context, eventId: Long): Intent {
        val uri = ContentUris.withAppendedId(CalendarContract.Events.CONTENT_URI, eventId)
        val view = Intent(Intent.ACTION_VIEW).setDataAndType(uri, EVENT_MIME_TYPE)
        return if (view.resolveActivity(context.packageManager) != null) view else openApp(context)
    }
}
