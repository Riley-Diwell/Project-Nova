package com.example.novav2.state

import com.example.novav2.ble.NovaDeviceRepository

/**
 * Named buzz patterns on the paired Nova device. Each is one PLAY_HAPTIC command
 * (docs/ble-protocol.md) that the firmware plays on its own clock - so the gaps are exact, which
 * they weren't when this side sequenced single pulses over BLE with delays. Ambient LED cues are
 * [DeviceLayers]'; this is only the one-off buzz.
 *
 * Every pattern is a silent no-op when nothing is connected, the same best-effort stance as
 * [NovaDeviceRepository.commandSender].
 */
object DeviceCue {
    /** Alternating on/off durations in ms, starting with on - at most 6 steps (the firmware's). */
    enum class Pattern(val stepsMs: List<Int>) {
        /** "Nova replied" / departure nudge - one 200 ms buzz. */
        NUDGE(listOf(200)),
        /** A reminder - two 250 ms pulses, distinct from [NUDGE]. */
        REMINDER(listOf(250, 180, 250)),
        REMINDER_IMPORTANT(listOf(250, 180, 250, 180, 250)),
        /** A button press was understood and done. */
        TICK(listOf(60)),
        /** A button press had nothing to act on (or Nova is busy) - two light taps. */
        NOTHING(listOf(40, 100, 40)),
    }

    /** STATUS: one short buzz per item, up to three (the firmware holds six steps); one long
     * buzz for none, so "nothing coming up" never feels like the press was missed. */
    fun playCount(count: Int): Boolean {
        val sender = NovaDeviceRepository.commandSender.value ?: return false
        sender.sendHapticPattern(countSteps(count))
        return true
    }

    internal fun countSteps(count: Int): List<Int> =
        if (count <= 0) listOf(500)
        else (0 until count.coerceAtMost(3)).flatMap { listOf(120, 200) }.dropLast(1)

    /** Plays [pattern]. Returns whether a device was connected to play it on. */
    fun play(pattern: Pattern): Boolean {
        val sender = NovaDeviceRepository.commandSender.value ?: return false
        sender.sendHapticPattern(pattern.stepsMs)
        return true
    }
}
