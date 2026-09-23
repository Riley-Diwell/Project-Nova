package com.example.novav2.service

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.example.novav2.R
import com.example.novav2.ble.NovaDeviceConnectionState
import com.example.novav2.ble.NovaDevicePairing
import com.example.novav2.ble.NovaDeviceRepository
import com.example.novav2.ble.NovaGattClient
import com.example.novav2.stt.VoskTranscriber
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Holds the GATT connection to the paired Nova device open for the life of the app,
 * mirroring [SignalMonitorService]'s foreground-service shape. Connects with
 * autoConnect=true — Android's own background/whitelist reconnect mode — so "stays
 * connected like AirPods" rides on the platform's own reconnection instead of a
 * hand-rolled poll-and-retry loop here.
 *
 * Deliberately does not yet use CompanionDeviceManager.startObservingDevicePresence.
 * Its API shape has changed significantly across Android versions (deprecated
 * callback-based on 26-32, broadcast-based ACTION_DEVICE_APPEARED from 33) and neither
 * path has been tested against real hardware. autoConnect's own reconnection is the
 * only mechanism used here for now; presence observation would mainly buy faster
 * wake-on-appear and reconnecting even after this process was killed, not
 * correctness — worth adding once there's a device to verify it against.
 */
class NovaDeviceService : Service(), NovaGattClient.Listener {

    private var gattClient: NovaGattClient? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    override fun onCreate() {
        super.onCreate()
        startForeground(NOTIFICATION_ID, buildNotification())
        // Started early, in parallel with the GATT connection - StorageService.unpack
        // takes a real amount of time on first run (copying the model to external
        // storage), and this way it's usually done well before the first utterance
        // the device sends actually finishes.
        VoskTranscriber.preload(applicationContext)
        connectIfPaired()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // A pairing action (DeviceScreen) can start this service again after it
        // already stopped itself for lack of a paired device - re-check rather
        // than relying only on onCreate's one-time read.
        if (gattClient == null) connectIfPaired()
        return START_STICKY
    }

    private fun connectIfPaired() {
        val address = NovaDevicePairing.pairedDeviceAddress(applicationContext) ?: run {
            stopSelf()
            return
        }
        val client = NovaGattClient(applicationContext, this)
        gattClient = client
        client.connect(address, autoConnect = true)
        // The same client instance is reused across autoConnect's own reconnects
        // (see NovaGattClient's class doc comment), so this one subscription
        // covers every utterance for the lifetime of this pairing, not just the
        // current connection.
        scope.launch {
            client.completedUtterances.collect { pcm -> onUtteranceRecorded(pcm) }
        }
        scope.launch { runKeepalive(client) }
    }

    /**
     * Pings the device every [PING_INTERVAL_MS] while connected — the firmware's
     * stale-connection watchdog needs this as proof the app is still alive (see
     * NovaGattClient.sendPing's doc comment), otherwise a connection left over
     * from a killed process (e.g. this app being redeployed) never gets torn down
     * and the device never resumes advertising, forcing a device restart to
     * reconnect at all.
     *
     * Also watches the device's own heartbeat the other direction: if this app
     * still thinks it's CONNECTED but hasn't heard one in [STALE_HEARTBEAT_TIMEOUT_MS],
     * the link is stuck on this end instead (e.g. a GATT callback that never fired)
     * - force a disconnect/reconnect rather than sitting in a dead CONNECTED state
     * indefinitely.
     */
    private suspend fun runKeepalive(client: NovaGattClient) {
        while (scope.isActive) {
            delay(PING_INTERVAL_MS)
            if (NovaDeviceRepository.connectionState.value != NovaDeviceConnectionState.CONNECTED) continue

            val lastHeartbeat = NovaDeviceRepository.lastHeartbeatAtMillis.value
            if (lastHeartbeat != null &&
                System.currentTimeMillis() - lastHeartbeat > STALE_HEARTBEAT_TIMEOUT_MS
            ) {
                client.disconnect()
                connectIfPaired()
                return
            }

            client.sendPing()
        }
    }

    /**
     * Transcribes one BLE utterance on-device (VoskTranscriber) and, if anything
     * intelligible came out, hands it to AssistVoiceService to process exactly like
     * the power-button gesture's own transcript: written to the chat thread,
     * POSTed through the same /event pipeline, spoken back via TTS. Best-effort -
     * a failed/empty transcription is dropped silently, same stance as every other
     * device/network touch in this codebase.
     */
    private fun onUtteranceRecorded(pcm: ByteArray) {
        scope.launch {
            val transcript = VoskTranscriber.transcribe(pcm) ?: return@launch
            // AssistVoiceService's manifest foregroundServiceType includes
            // "microphone", which Android 14+ refuses to start without RECORD_AUDIO
            // already granted - true here even though this particular turn never
            // touches the phone's own mic, since the type is declared per service
            // class, not per call. Same permission AssistTrampolineActivity already
            // requires before starting this same service for the phone-mic path.
            val hasMicPermission = ContextCompat.checkSelfPermission(
                applicationContext, Manifest.permission.RECORD_AUDIO,
            ) == PackageManager.PERMISSION_GRANTED
            if (!hasMicPermission) return@launch
            ContextCompat.startForegroundService(
                applicationContext,
                Intent(applicationContext, AssistVoiceService::class.java)
                    .putExtra(AssistVoiceService.EXTRA_TRANSCRIPT, transcript),
            )
        }
    }

    override fun onConnectionStateChanged(state: NovaDeviceConnectionState) {
        NovaDeviceRepository.setConnectionState(state)
        NovaDeviceRepository.setCommandSender(
            if (state == NovaDeviceConnectionState.CONNECTED) gattClient else null
        )
    }

    override fun onDestroy() {
        gattClient?.disconnect()
        gattClient = null
        NovaDeviceRepository.setCommandSender(null)
        NovaDeviceRepository.setConnectionState(NovaDeviceConnectionState.DISCONNECTED)
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun buildNotification(): Notification {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Nova device connection", NotificationManager.IMPORTANCE_MIN)
            )
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Nova")
            .setContentText("Connected to your Nova device")
            .setSmallIcon(R.mipmap.ic_launcher)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "nova_device_connection"
        // 42 = SignalMonitorService, 43 = AmbientNotifier, 44 = AssistVoiceService -
        // each foreground/posted notification in the app needs its own id or one
        // can silently replace/cancel another's.
        private const val NOTIFICATION_ID = 45

        // Matches firmware's own HEARTBEAT_INTERVAL_MS (5000) - pinging on the same
        // cadence the firmware already broadcasts on keeps both sides' watchdogs
        // reading the same "still alive" cadence rather than picking an unrelated number.
        private const val PING_INTERVAL_MS = 5_000L
        // 3x the heartbeat interval - enough slack for BLE latency/a missed
        // notification or two without false-tripping on a perfectly healthy link.
        private const val STALE_HEARTBEAT_TIMEOUT_MS = 15_000L
    }
}
