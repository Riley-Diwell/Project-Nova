package com.example.novav2.state

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.example.novav2.R
import com.example.novav2.ble.NovaCommandSender
import com.example.novav2.ble.NovaDeviceRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Posts a local "Nova nudges" notification - the delivery mechanism for anything Nova says
 * without a live conversation to speak into: an ambient heads-up from
 * [com.example.novav2.service.SignalMonitorService], or a precise leave-now alert from
 * [com.example.novav2.service.DepartureAlarmReceiver]. Both need the exact same channel and
 * permission check, so it lives here rather than duplicated in each caller.
 */
object AmbientNotifier {
    // "_v2": IMPORTANCE_HIGH only takes effect on a channel's *first* creation - Android locks
    // importance against later reprogramming (the user can still raise/lower it themselves in
    // system settings, but createNotificationChannel() silently no-ops on an existing id). A
    // device that already has the old "nova_ambient" channel from DEFAULT-importance code would
    // otherwise keep posting quiet, no-heads-up notifications forever despite this change.
    private const val CHANNEL_ID = "nova_ambient_v2"
    private const val NOTIFICATION_ID = 43

    /** How urgently the paired device's LED should flash alongside a notification - the two
     * departure moments AmbientCheckRunner/DepartureAlarmReceiver cover read as one escalating
     * cue rather than two identical buzzes: slow while there's still time to wrap up, fast once
     * it's the real leave-by moment. `null` (the default) means no LED pattern at all - e.g.
     * AssistVoiceService's own ambient replies aren't a departure cue and shouldn't borrow one. */
    enum class LedFlash { SLOW, FAST }

    private const val FLASH_PULSE_COUNT = 4
    private const val FLASH_PULSE_DURATION_MS = 200
    private const val SLOW_FLASH_GAP_MS = 1000L
    private const val FAST_FLASH_GAP_MS = 250L

    // Fire-and-forget, same stance as the haptic pulse below: notify() itself isn't suspend
    // (two of its three callers - DepartureAlarmReceiver.onReceive and
    // AssistVoiceService.finishTurn - aren't either), so the multi-pulse pattern needs its own
    // scope rather than making every caller launch one just to flash an LED.
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = context.getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    "Nova nudges",
                    // HIGH, not DEFAULT: a leave-now nudge has a real deadline behind it, so it
                    // needs the heads-up banner + sound, not a silent add to the shade.
                    NotificationManager.IMPORTANCE_HIGH,
                )
            )
        }
    }

    /** Returns whether a notification was actually posted - false (never an exception) if [text]
     * is blank or POST_NOTIFICATIONS isn't granted. Callers need this: "the backend had
     * something to say" and "the device actually showed it" are different facts, and
     * AmbientCheckRunner's SPOKE/QUIET/BLOCKED outcome depends on telling them apart rather than
     * reporting success just because delivery was attempted. */
    fun notify(context: Context, text: String, ledFlash: LedFlash? = null): Boolean {
        if (text.isBlank()) return false

        // Independent of the phone-notification permission check below, and of this
        // function's own return value (that's specifically about the phone
        // notification - see the doc comment) - a wearable buzz and a phone banner
        // are two separate delivery channels, one denying POST_NOTIFICATIONS
        // shouldn't silence the other. No-op if nothing is connected right now
        // (NovaDeviceRepository.commandSender is only non-null while a device is),
        // same best-effort stance as everything else here.
        val sender = NovaDeviceRepository.commandSender.value
        sender?.sendHapticPulse(durationMs = 200)
        if (sender != null && ledFlash != null) {
            scope.launch { flashLed(sender, ledFlash) }
        }

        if (ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            return false
        }

        NotificationManagerCompat.from(context).notify(
            NOTIFICATION_ID,
            NotificationCompat.Builder(context, CHANNEL_ID)
                .setContentTitle("Nova")
                .setContentText(text)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setAutoCancel(true)
                .build()
        )
        return true
    }

    /** [FLASH_PULSE_COUNT] LED pulses spaced by [SLOW_FLASH_GAP_MS]/[FAST_FLASH_GAP_MS] depending
     * on [pattern] - the firmware only knows how to do one pulse at a time (see
     * docs/ble-protocol.md's LED_PULSE command), so "slow" vs "fast" flashing is purely this
     * side's sequencing, not a firmware concept. Each [NovaCommandSender.sendLedPulse] call
     * enqueues on the same serialised BLE write queue NovaGattClient already uses for every
     * command, so back-to-back calls here are safe the same way sendHapticPulse/sendLedPulse
     * already are individually - this just calls it on a delay instead of once. */
    private suspend fun flashLed(sender: NovaCommandSender, pattern: LedFlash) {
        val gapMs = if (pattern == LedFlash.FAST) FAST_FLASH_GAP_MS else SLOW_FLASH_GAP_MS
        repeat(FLASH_PULSE_COUNT) { i ->
            sender.sendLedPulse(FLASH_PULSE_DURATION_MS)
            if (i < FLASH_PULSE_COUNT - 1) delay(gapMs)
        }
    }
}
