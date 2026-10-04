package com.example.novav2.state

import android.content.Context
import com.example.novav2.ble.NovaBleProtocol
import com.example.novav2.ble.NovaDeviceRepository
import com.example.novav2.ble.NovaLedLayer
import com.example.novav2.data.NovaDatabase
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

/**
 * What the paired device's LED should be showing in the background - the ambient half of the
 * device, next to [DeviceCue]'s one-off buzzes. Each [Cue] sits in a [Slot] on the firmware's
 * layer stack (docs/ble-protocol.md "LED layers"); the device shows the highest-priority slot and
 * plays the pattern on its own clock, so a reminder can pulse for hours with no BLE traffic.
 *
 * This object is the source of truth, not the device: the firmware drops every layer when the
 * link goes down, and [onConnected] puts back whatever still applies. Like every device command,
 * a change made while nothing is connected is simply sent on the next connect.
 *
 * Rhythm carries the meaning and colour is secondary - the LED is red/green only, which is the
 * pair red-green colour blindness can't tell apart. Each cue's colour is a user setting
 * ([DevicePreferences.cueMixes], loaded into [setColours]); [Cue]'s own colours are the defaults.
 */
object DeviceLayers {
    /** A place on the device's layer stack. Higher [priority] shows over lower. */
    enum class Slot(val id: Int, val priority: Int) {
        REMINDER(1, 10),
        DEPARTURE(2, 20),
        /** Short-lived feedback for a live exchange (DeviceInteraction) - over the alerts. */
        INTERACTION(3, 30),
        /** The settings screen's "preview on device" - over everything, gone on its own after
         * [PREVIEW_MILLIS], and never in [active], so a preview can't touch a real alert. */
        PREVIEW(4, 40),
    }

    /** SLOW/MEDIUM/FAST are the three ambient tiers: short flashes, long gaps - the LED is on for
     * a small fraction of each period, which is what keeps an hour of pulsing cheap on battery. */
    enum class Rhythm(val pattern: Int, val periodMs: Int, val onMs: Int) {
        SLOW(NovaBleProtocol.LedPattern.BLINK, 3_000, 150),
        MEDIUM(NovaBleProtocol.LedPattern.BLINK, 1_000, 150),
        FAST(NovaBleProtocol.LedPattern.BLINK, 330, 120),
        BREATHE(NovaBleProtocol.LedPattern.BREATHE, 1_500, 0),
    }

    /** Declared in rising urgency within each slot - [show]'s "never downgrade" rule compares
     * declaration order. */
    enum class Cue(val slot: Slot, val rhythm: Rhythm, val red: Int, val green: Int) {
        /** A reminder went off and hasn't been answered. Amber. */
        REMINDER(Slot.REMINDER, Rhythm.SLOW, 255, 180),
        /** "You should leave in N minutes" (AmbientCheckRunner). Orange. */
        LEAVE_SOON(Slot.DEPARTURE, Rhythm.MEDIUM, 255, 70),
        /** The leave-by moment itself (DepartureAlarmReceiver). Red. */
        LEAVE_NOW(Slot.DEPARTURE, Rhythm.FAST, 255, 0),
        /** Nova is working on what you just said. Green, breathing. */
        THINKING(Slot.INTERACTION, Rhythm.BREATHE, 0, 255),
    }

    /** A cue currently showing, and the words behind it (what a triple press reads aloud). */
    data class Shown(val cue: Cue, val text: String?)

    /** How long an unanswered reminder keeps the LED pulsing after it last went off. */
    const val REMINDER_GLOW_MILLIS = 6 * 60 * 60_000L

    const val PREVIEW_MILLIS = 5_000L

    /**
     * The settings screen's colour slider: one number for a red/green-only LED, 0 = green,
     * 100 = red. The stronger channel always stays at full, so the middle is yellow rather than
     * a dim brown, and the defaults (amber 255/180, orange 255/70) are reachable - a plain linear
     * mix never has both channels high.
     */
    object Mix {
        fun toColour(mix: Int): Pair<Int, Int> {
            val m = mix.coerceIn(0, 100)
            return Pair(
                (255 * minOf(1.0, 2.0 * m / 100)).roundToInt(),
                (255 * minOf(1.0, 2.0 * (100 - m) / 100)).roundToInt(),
            )
        }

        fun of(red: Int, green: Int): Int =
            if (red >= green) (100 - 50.0 * green / 255).roundToInt()
            else (50.0 * red / 255).roundToInt()
    }

    /** Cue -> slider value, for the cues the user has recoloured. Volatile rather than read from
     * SharedPreferences because [show] and [wireLayer] have no Context; NovaApplication loads it. */
    @Volatile private var colours: Map<Cue, Int> = emptyMap()

    fun setColours(mixes: Map<Cue, Int>) {
        colours = mixes
    }

    /** [cue]'s (red, green) as the user set it, or its default. */
    fun colourOf(cue: Cue): Pair<Int, Int> = colours[cue]?.let(Mix::toColour) ?: Pair(cue.red, cue.green)

    private data class Active(val cue: Cue, val expiresAtMillis: Long?, val text: String?) {
        fun expired(nowMillis: Long) = expiresAtMillis != null && nowMillis >= expiresAtMillis
    }

    private val lock = Any()
    private val active = mutableMapOf<Slot, Active>()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /**
     * Shows [cue] for [durationMillis] (null = until [clear]ed). A lower tier never replaces a live
     * higher one in the same slot: an ambient check that still says "leave soon" after the
     * leave-now alarm went off must not calm the LED back down.
     */
    fun show(
        cue: Cue,
        durationMillis: Long?,
        nowMillis: Long = System.currentTimeMillis(),
        text: String? = null,
    ) {
        val next = Active(cue, durationMillis?.let { nowMillis + it }, text)
        synchronized(lock) {
            val current = active[cue.slot]?.takeUnless { it.expired(nowMillis) }
            if (current != null && current.cue > cue) return
            // Re-sending an identical layer would restart its blink mid-cycle for nothing -
            // ReminderScheduler.reconcile runs on every reminder edit.
            if (current == next) return
            active[cue.slot] = next
        }
        wireLayer(cue, next.expiresAtMillis, nowMillis)?.let { layer ->
            NovaDeviceRepository.commandSender.value?.sendSetLayer(layer)
        }
    }

    /** What [slot] is showing right now, if anything. */
    fun shown(slot: Slot, nowMillis: Long = System.currentTimeMillis()): Shown? =
        synchronized(lock) { active[slot]?.takeUnless { it.expired(nowMillis) }?.let { Shown(it.cue, it.text) } }

    fun clear(slot: Slot) {
        val removed = synchronized(lock) { active.remove(slot) }
        if (removed != null) NovaDeviceRepository.commandSender.value?.sendClearLayer(slot.id)
    }

    /** The device starts every connection with no layers - see the class comment. Clears first
     * anyway: a link that survived this app's process restarting kept the old process's layers. */
    fun onConnected(context: Context) {
        val sender = NovaDeviceRepository.commandSender.value ?: return
        sender.sendClearLayer(NovaBleProtocol.LAYER_ID_ALL)
        resend()
        // A fresh process has nothing in [active] yet - the reminder slot comes from the table.
        val app = context.applicationContext
        scope.launch { syncReminders(app) }
    }

    /** Sends every live layer again - after a colour change, so it shows without waiting for the
     * next alert. A SET_LAYER for a slot the device already shows replaces it in place. */
    fun resend(nowMillis: Long = System.currentTimeMillis()) {
        val sender = NovaDeviceRepository.commandSender.value ?: return
        val live = synchronized(lock) {
            active.entries.removeAll { it.value.expired(nowMillis) }
            active.values.toList()
        }
        live.forEach { a -> wireLayer(a.cue, a.expiresAtMillis, nowMillis)?.let(sender::sendSetLayer) }
    }

    /** Shows [cue], in its current colour, on the [Slot.PREVIEW] slot for [PREVIEW_MILLIS]. The
     * device's own timeout ends it, so there is nothing to restore. The firmware holds four
     * layers: with every slot live plus its own not-connected layer, this evicts REMINDER, which
     * comes back on the next connect or reminder change. */
    fun preview(cue: Cue, nowMillis: Long = System.currentTimeMillis()) {
        wireLayer(cue, nowMillis + PREVIEW_MILLIS, nowMillis, Slot.PREVIEW)?.let { layer ->
            NovaDeviceRepository.commandSender.value?.sendSetLayer(layer)
        }
    }

    /** Pulses while any reminder is fired-but-unanswered, for up to [REMINDER_GLOW_MILLIS] after
     * the latest one went off. Called from [ReminderScheduler.reconcile], which every reminder
     * change already goes through. */
    suspend fun syncReminders(context: Context) {
        val latestFired = NovaDatabase.getInstance(context.applicationContext).reminderDao().latestFiredAt()
        val now = System.currentTimeMillis()
        val remaining = latestFired?.let { it + REMINDER_GLOW_MILLIS - now }
        if (remaining != null && remaining > 0) {
            show(Cue.REMINDER, remaining, now)
        } else {
            clear(Slot.REMINDER)
        }
    }

    /** [cue] as the firmware's SET_LAYER wants it, its timeout counted from [nowMillis]; null if
     * it has already expired. Rounds up, so a layer never goes dark before the phone said. */
    internal fun wireLayer(cue: Cue, expiresAtMillis: Long?, nowMillis: Long, slot: Slot = cue.slot): NovaLedLayer? {
        val timeoutSeconds = if (expiresAtMillis == null) 0 else {
            val left = expiresAtMillis - nowMillis
            if (left <= 0) return null
            ((left + 999) / 1000).toInt().coerceAtLeast(1)
        }
        val (red, green) = colourOf(cue)
        return NovaLedLayer(
            id = slot.id,
            priority = slot.priority,
            red = red,
            green = green,
            pattern = cue.rhythm.pattern,
            periodMs = cue.rhythm.periodMs,
            onMs = cue.rhythm.onMs,
            timeoutSeconds = timeoutSeconds,
        )
    }

    /** Tests only - this is process-wide state. */
    internal fun reset() = synchronized(lock) {
        active.clear()
        colours = emptyMap()
    }
}
