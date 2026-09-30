package com.example.novav2.state

import com.example.novav2.ble.NovaCommandSender
import com.example.novav2.ble.NovaDeviceRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Named haptic/LED patterns on the paired Nova device. The firmware only does one pulse per
 * command (docs/ble-protocol.md's HAPTIC_PULSE / LED_PULSE), so a pattern is purely this side's
 * sequencing: each call enqueues on NovaGattClient's serialised write queue, spaced by a delay.
 * If BLE timing ever proves too jittery for these to feel distinct, a firmware pattern command is
 * the fix - nothing that calls [play] would change.
 *
 * Every pattern is a silent no-op when nothing is connected, the same best-effort stance as
 * [NovaDeviceRepository.commandSender].
 */
object DeviceCue {
    enum class Pattern(val pulseMs: Int, val pulses: Int, val led: AmbientNotifier.LedFlash?) {
        /** "Nova replied" / departure nudge - one 200 ms buzz. */
        NUDGE(200, 1, null),
        /** A reminder - two 250 ms pulses plus a slow LED, distinct from [NUDGE]. */
        REMINDER(250, 2, AmbientNotifier.LedFlash.SLOW),
        REMINDER_IMPORTANT(250, 3, AmbientNotifier.LedFlash.SLOW),
    }

    private const val PULSE_GAP_MS = 180L
    private const val LED_PULSE_COUNT = 4
    private const val LED_PULSE_MS = 200
    private const val SLOW_LED_GAP_MS = 1000L
    private const val FAST_LED_GAP_MS = 250L

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    /** Plays [pattern], with [led] overriding the pattern's own LED if given. Returns whether a
     * device was connected to play it on. */
    fun play(pattern: Pattern, led: AmbientNotifier.LedFlash? = pattern.led): Boolean {
        val sender = NovaDeviceRepository.commandSender.value ?: return false
        scope.launch {
            repeat(pattern.pulses) { i ->
                sender.sendHapticPulse(pattern.pulseMs)
                if (i < pattern.pulses - 1) delay(pattern.pulseMs + PULSE_GAP_MS)
            }
        }
        if (led != null) scope.launch { flashLed(sender, led) }
        return true
    }

    private suspend fun flashLed(sender: NovaCommandSender, flash: AmbientNotifier.LedFlash) {
        val gapMs = if (flash == AmbientNotifier.LedFlash.FAST) FAST_LED_GAP_MS else SLOW_LED_GAP_MS
        repeat(LED_PULSE_COUNT) { i ->
            sender.sendLedPulse(LED_PULSE_MS)
            if (i < LED_PULSE_COUNT - 1) delay(gapMs)
        }
    }
}
