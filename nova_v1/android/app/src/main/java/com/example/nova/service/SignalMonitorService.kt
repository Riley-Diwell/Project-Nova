package com.example.nova.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.example.nova.R
import com.example.nova.state.ActivitySignal
import com.example.nova.state.LocationSignal
import com.example.nova.state.SensorSignal
import com.example.nova.state.SignalRepository
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
 */
class SignalMonitorService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onCreate() {
        super.onCreate()
        startForeground(NOTIFICATION_ID, buildNotification())

        ActivitySignal.startUpdates(this)
        SensorSignal.startUpdates(this)

        scope.launch {
            while (true) {
                LocationSignal.refresh(applicationContext)
                SignalRepository.update(applicationContext)
                delay(REFRESH_INTERVAL_MILLIS)
            }
        }
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
    }
}
