package com.example.novav2.widget

import android.content.Context
import android.util.Log
import com.example.novav2.auth.AuthRepository
import com.example.novav2.ble.NovaDeviceConnectionState
import com.example.novav2.ble.NovaDevicePairing
import com.example.novav2.ble.NovaDeviceRepository
import com.example.novav2.data.NovaDatabase
import com.example.novav2.state.CalendarSignal
import com.example.novav2.state.DepartureStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.time.ZoneId

/**
 * Gathers [WidgetSnapshotBuilder]'s inputs from what is already on the phone. Never throws: a
 * source that can't be read (no calendar permission, a database error) just drops its section, so
 * the widget always has something sensible to draw.
 *
 * The device state is in-memory ([NovaDeviceRepository]) and only true while this process holds
 * the link - which it does whenever [com.example.novav2.service.NovaDeviceService] is running. A
 * process started just to draw the widget has no link, and "not connected" is then the truth.
 */
object WidgetSnapshotLoader {
    private const val TAG = "WidgetSnapshotLoader"
    private const val CALENDAR_WINDOW_MILLIS = 24 * 60 * 60_000L

    suspend fun load(context: Context): WidgetSnapshot = withContext(Dispatchers.IO) {
        if (!AuthRepository.isSignedIn) return@withContext WidgetSnapshot.signedOut()
        val app = context.applicationContext
        val now = System.currentTimeMillis()

        val reminders = attempt("reminders") { NovaDatabase.getInstance(app).reminderDao().active() }.orEmpty()
        val events = attempt("calendar") { CalendarSignal.rangeSnapshot(app, now, now + CALENDAR_WINDOW_MILLIS) }
        val departure = attempt("departure") { DepartureStore.load(app) }
        val device = attempt("device") { device(app) } ?: WidgetDevice(paired = false, connected = false, batteryPercent = null)

        WidgetSnapshotBuilder.build(reminders, events, departure, device, now, ZoneId.systemDefault())
    }

    fun device(context: Context) = WidgetDevice(
        paired = NovaDevicePairing.isPaired(context),
        connected = NovaDeviceRepository.connectionState.value == NovaDeviceConnectionState.CONNECTED,
        batteryPercent = NovaDeviceRepository.battery.value?.percent,
    )

    private inline fun <T> attempt(what: String, read: () -> T): T? =
        try {
            read()
        } catch (e: Exception) {
            Log.w(TAG, "widget: couldn't read $what", e)
            null
        }
}
