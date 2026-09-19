package com.example.novav2.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.example.novav2.R
import com.example.novav2.state.ActivitySignal
import com.example.novav2.state.AmbientCheckRunner
import com.example.novav2.state.AmbientNotifier
import com.example.novav2.state.LocationSignal
import com.example.novav2.state.SensorSignal
import com.example.novav2.state.SignalRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Keeps [SignalRepository] fresh every [REFRESH_INTERVAL_MILLIS] independent of whether the
 * app UI is open. Runs as a foreground service (required by Android for any background work
 * this frequent) with a low-priority "Nova is monitoring your signals" notification.
 *
 * Also the ambient producer: every [SignalRepository.AMBIENT_CHECK_INTERVAL_MILLIS] (a multiple
 * of the refresh interval, not a second coroutine - both concerns read the same freshly-collected
 * UserState) it runs [AmbientCheckRunner] so navigation_departure_time and anything else with
 * enough gain gets to act on inferred state, unprompted, the way a voice turn already can. Gated
 * on-device first (see [maybePostAmbientEvent]) so most ticks never touch the network - only a
 * genuinely upcoming commitment is worth spending a request on.
 */
class SignalMonitorService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onCreate() {
        super.onCreate()
        startForeground(NOTIFICATION_ID, buildNotification())
        AmbientNotifier.ensureChannel(this)

        ActivitySignal.startUpdates(this)
        SensorSignal.startUpdates(this)

        scope.launch {
            var ticksSinceAmbientCheck = 0
            SignalRepository.scheduleNextAmbientCheck(
                System.currentTimeMillis() + SignalRepository.AMBIENT_CHECK_INTERVAL_MILLIS
            )
            while (true) {
                LocationSignal.refresh(applicationContext)
                SignalRepository.update(applicationContext)

                ticksSinceAmbientCheck++
                if (ticksSinceAmbientCheck >= TICKS_PER_AMBIENT_CHECK) {
                    ticksSinceAmbientCheck = 0
                    maybePostAmbientEvent()
                    SignalRepository.scheduleNextAmbientCheck(
                        System.currentTimeMillis() + SignalRepository.AMBIENT_CHECK_INTERVAL_MILLIS
                    )
                }

                delay(REFRESH_INTERVAL_MILLIS)
            }
        }
    }

    /**
     * Only worth a network call if something is actually coming up - most 10-minute ticks have
     * nothing new to say, and a periodic ping should not cost a Directions/Places lookup (see
     * navigation.py's resolved-place cache) on every single one of them regardless. StateScreen's
     * manual "check now" debug button skips this gate on purpose - see [AmbientCheckRunner].
     */
    private suspend fun maybePostAmbientEvent() {
        val userState = SignalRepository.userState.value ?: return
        if (!AmbientCheckRunner.hasImminentCommitment(userState, AMBIENT_HORIZON_MINUTES)) return
        AmbientCheckRunner.run(applicationContext, userState)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        scope.cancel()
        ActivitySignal.stopUpdates(this)
        SensorSignal.stopUpdates(this)
        super.onDestroy()
    }

    private fun buildNotification(): Notification {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(NotificationManager::class.java)
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Signal monitoring",
                NotificationManager.IMPORTANCE_LOW,
            )
            manager.createNotificationChannel(channel)
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Nova")
            .setContentText("Monitoring your signals in the background")
            .setSmallIcon(R.mipmap.ic_launcher)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "signal_monitor"
        private const val NOTIFICATION_ID = 42
        private const val REFRESH_INTERVAL_MILLIS = 10_000L

        // A multiple of REFRESH_INTERVAL_MILLIS, not an independent timer - see the class doc
        // comment. SignalRepository.AMBIENT_CHECK_INTERVAL_MILLIS matches TimeEvent's own
        // original docstring (schemas/event.py: "every 10 minutes"). Every check inside
        // AMBIENT_HORIZON_MINUTES is a real backend turn whether or not it ends up authorised to
        // speak (see maybePostAmbientEvent), so this and the horizon below together bound how
        // many Claude calls one imminent class costs - at 10/60 that's up to 6 checks per class.
        private const val TICKS_PER_AMBIENT_CHECK =
            (SignalRepository.AMBIENT_CHECK_INTERVAL_MILLIS / REFRESH_INTERVAL_MILLIS).toInt()

        // On-device pre-filter: only a commitment inside this horizon makes an ambient ping
        // worth its network/API cost. Still wider than navigation.py's own SLACK_HORIZON_MINUTES
        // (30) on purpose - this just decides whether to ask at all; the backend's error() term
        // still decides whether the answer is actually urgent enough to speak.
        private const val AMBIENT_HORIZON_MINUTES = 60
    }
}
