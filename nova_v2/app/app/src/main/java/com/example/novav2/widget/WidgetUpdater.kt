package com.example.novav2.widget

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.glance.appwidget.updateAll
import com.example.novav2.ble.NovaDeviceRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * The widget never polls. It is redrawn when one of its inputs changes - every caller of
 * [requestUpdate] is one of those - plus one alarm at the next moment its contents would change
 * on their own ([scheduleNextBoundary]), with nova_widget_info.xml's 30-minute
 * updatePeriodMillis as the floor if everything else is missed.
 *
 * Callers: ReminderScheduler.reconcile (every reminder change, boot, time change, app start),
 * DepartureAlarmScheduler and DepartureAlarmReceiver, DeviceInteraction's departure acknowledge,
 * AuthRepository.publish (sign-in, sign-out and a session dying on its own), and
 * SignalMonitorService's ambient tick (calendar changes, every ~10 min while signed in).
 * NovaDeviceService runs [followDevice].
 */
object WidgetUpdater {
    private const val TAG = "WidgetUpdater"

    /** PendingIntent request code for the boundary alarm - see ReminderScheduler's list. */
    private const val REQUEST_CODE = 500

    /** reconcile() can run several times in a row; one redraw covers them all. */
    private const val DEBOUNCE_MILLIS = 1_000L

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val lock = Any()
    private var pending: Job? = null

    fun requestUpdate(context: Context) {
        val app = context.applicationContext
        synchronized(lock) {
            if (pending?.isActive == true) return
            pending = scope.launch {
                delay(DEBOUNCE_MILLIS)
                // Cleared before drawing, not after: a change that lands while this redraw is
                // reading its inputs gets a redraw of its own instead of being swallowed.
                synchronized(lock) { pending = null }
                updateNow(app)
            }
        }
    }

    /** Bumped on every redraw. A Glance session outlives the provideGlance call that loaded its
     * snapshot, and updateAll only recomposes a running session - so NovaWidget reloads its
     * snapshot whenever this moves, or it would redraw the data it started with. */
    private val _refreshes = MutableStateFlow(0L)
    val refreshes: StateFlow<Long> = _refreshes

    suspend fun updateNow(context: Context) {
        _refreshes.update { it + 1 }
        try {
            NovaWidget().updateAll(context.applicationContext)
        } catch (e: Exception) {
            Log.w(TAG, "widget update failed", e)
        }
    }

    /**
     * One inexact alarm at [atMillis] (a null cancels it). RTC rather than RTC_WAKEUP: the
     * widget is only seen with the screen on, so the redraw can wait for the phone to wake.
     * Fixed request code, so each update replaces the previous boundary rather than adding one.
     */
    fun scheduleNextBoundary(context: Context, atMillis: Long?) {
        val app = context.applicationContext
        val alarmManager = app.getSystemService(AlarmManager::class.java)
        val pendingIntent = PendingIntent.getBroadcast(
            app, REQUEST_CODE, Intent(app, WidgetBoundaryReceiver::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        if (atMillis == null) alarmManager.cancel(pendingIntent)
        else alarmManager.setAndAllowWhileIdle(AlarmManager.RTC, atMillis, pendingIntent)
    }

    /**
     * Redraws when the device connects or disconnects, or its battery moves by 5% or more
     * ([WidgetSnapshotBuilder.deviceChangeWorthUpdate]) - the battery is reported every ~15 s,
     * which is far more often than anyone needs a widget redrawn. Runs for as long as
     * NovaDeviceService's scope does.
     */
    suspend fun followDevice(context: Context) {
        val app = context.applicationContext
        var shown: WidgetDevice? = null
        combine(NovaDeviceRepository.connectionState, NovaDeviceRepository.battery) { _, _ ->
            WidgetSnapshotLoader.device(app)
        }.collect { device ->
            if (WidgetSnapshotBuilder.deviceChangeWorthUpdate(shown, device)) {
                shown = device
                requestUpdate(app)
            }
        }
    }
}

/** The boundary alarm ([WidgetUpdater.scheduleNextBoundary]) - redraws, which sets the next one. */
class WidgetBoundaryReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val result = goAsync()
        CoroutineScope(Dispatchers.Default).launch {
            try {
                WidgetUpdater.updateNow(context)
            } finally {
                result.finish()
            }
        }
    }
}
