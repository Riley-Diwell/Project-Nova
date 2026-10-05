package com.example.novav2.state

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import com.example.novav2.data.NovaDatabase
import com.example.novav2.data.ReminderEntity
import com.example.novav2.service.ReminderAlarmReceiver
import com.example.novav2.widget.WidgetUpdater
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Keeps exactly one alarm set: for whichever reminder is due next.
 * [reconcile] is idempotent - it re-derives that alarm from the table every time - so it
 * is simply called after every change and on every occasion the platform may have dropped it:
 * boot, app update, time/zone change, exact-alarm permission change, and app start (every Run
 * from Android Studio force-stops the app, and a force-stop cancels all its alarms).
 *
 * Exact where allowed: setExactAndAllowWhileIdle fires on time in Doze. Without the permission
 * (API 31-32 with SCHEDULE_EXACT_ALARM revoked) it falls back to setAndAllowWhileIdle, which can
 * be minutes late, and [exactAlarmsAllowed] drives the Reminders screen's "may be late" banner.
 *
 * Place reminders are fired by geofences rather than this alarm, so reconcile also hands over to
 * [GeofenceRegistrar] - every caller that keeps the alarm right keeps the geofences right too.
 * The same goes for the device's reminder LED ([DeviceLayers.syncReminders]): it pulses while
 * something has fired unanswered, and every answer comes through here. And for the home-screen
 * widget ([WidgetUpdater]), which shows the next reminders.
 *
 * PendingIntent request codes in this app: 100 = DepartureAlarmScheduler, 200 = this,
 * 400 = GeofenceRegistrar, 500 = WidgetUpdater's boundary alarm, 1001 = ActivitySignal,
 * 3000+ = ReminderNotifier's per-reminder actions.
 */
object ReminderScheduler {
    private const val REQUEST_CODE = 200

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val mutex = Mutex()

    private val _exactAlarmsAllowed = MutableStateFlow(true)
    val exactAlarmsAllowed: StateFlow<Boolean> = _exactAlarmsAllowed

    fun canScheduleExact(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.S ||
            context.getSystemService(AlarmManager::class.java).canScheduleExactAlarms()

    suspend fun reconcile(context: Context) {
        reconcileAlarm(context)
        GeofenceRegistrar.reconcile(context)
        DeviceLayers.syncReminders(context)
        WidgetUpdater.requestUpdate(context)
    }

    private suspend fun reconcileAlarm(context: Context) = mutex.withLock {
        val app = context.applicationContext
        val alarmManager = app.getSystemService(AlarmManager::class.java)
        val pendingIntent = pendingIntent(app)
        val exact = canScheduleExact(app)
        _exactAlarmsAllowed.value = exact

        // NO_TRIGGER is a place reminder waiting on its place - MIN only returns it when nothing
        // else needs the alarm.
        val next = NovaDatabase.getInstance(app).reminderDao().nextTrigger()
            ?.takeIf { it != ReminderEntity.NO_TRIGGER }
        if (next == null) {
            alarmManager.cancel(pendingIntent)
            return@withLock
        }
        // A trigger already in the past (the phone was off, or the alarm was dropped) fires
        // straight away - delivery labels it "Missed at ..." if it is more than 5 minutes late.
        val at = maxOf(next, System.currentTimeMillis() + 1_000L)
        if (exact) {
            // Checked above, never assumed: on API 31+ setExact* throws SecurityException
            // without the permission.
            try {
                alarmManager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pendingIntent)
                return@withLock
            } catch (e: SecurityException) {
                _exactAlarmsAllowed.value = false
            }
        }
        alarmManager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pendingIntent)
    }

    /** For callers with no coroutine of their own (Activity/Service onCreate). */
    fun reconcileAsync(context: Context) {
        val app = context.applicationContext
        scope.launch { reconcile(app) }
    }

    /** The system screen where the user can allow exact alarms (API 31+), for the banner. */
    fun exactAlarmSettingsIntent(context: Context): Intent? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:${context.packageName}"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        } else null

    private fun pendingIntent(context: Context): PendingIntent =
        PendingIntent.getBroadcast(
            context, REQUEST_CODE,
            Intent(context, ReminderAlarmReceiver::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
}
