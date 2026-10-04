package com.example.novav2.state

import android.content.Context
import com.example.novav2.ble.NovaDeviceEvent
import com.example.novav2.ble.NovaDeviceRepository
import com.example.novav2.data.NovaDatabase
import com.example.novav2.model.ReminderStatus
import com.example.novav2.service.AssistVoiceService
import com.example.novav2.service.ReminderSpeechService
import com.example.novav2.state.DeviceButtonPolicy.Action
import com.example.novav2.state.DeviceButtonPolicy.Mode
import com.example.novav2.state.DeviceButtonPolicy.ModeState
import com.example.novav2.widget.WidgetUpdater
import kotlinx.coroutines.flow.filterIsInstance

/**
 * The device button's state machine at runtime: tracks the interaction mode (idle / Nova
 * thinking / Nova just replied), keeps the device told of it (SET_MODE), and carries out what
 * [DeviceButtonPolicy] decides each press means. Ambient alerts aren't a mode here - they are
 * whatever [DeviceLayers] is showing, read at the moment of the press.
 *
 * Modes are entered by AssistVoiceService ([thinking] when a turn starts, [replied] with its
 * answer, [replyFinished] once it has been spoken) and expire by themselves - on the phone lazily,
 * on the device by the timeout sent with them - so nothing has to remember to end them.
 */
object DeviceInteraction {
    /** How long after a reply finishes speaking a press still means "repeat that". */
    const val REPLY_WINDOW_MILLIS = 15_000L
    /** How long after a question finishes speaking presses can answer it. Must stay under the
     * server's _PENDING_CONFIRMATION_TTL (3 min, intent_surface.py), so a press can never answer
     * a question the server has already dropped. */
    const val ANSWER_WINDOW_MILLIS = 60_000L
    /** Covers a slow network turn; a turn that dies without replying stops "thinking" by itself. */
    private const val THINKING_TIMEOUT_MILLIS = 60_000L
    private const val STATUS_HORIZON_MILLIS = 60 * 60_000L
    private const val RECENT_MODES = 4

    private val lock = Any()
    private var current = ModeState(Mode.IDLE, 0, null)
    private val recent = ArrayDeque<ModeState>()
    private var lastToken = 0

    /** What Nova last said through AssistVoiceService - the REPEAT_LAST_REPLY idle action. */
    @Volatile var lastReply: String? = null
        private set

    fun thinking() {
        enter(Mode.THINKING, THINKING_TIMEOUT_MILLIS, null)
        DeviceLayers.show(DeviceLayers.Cue.THINKING, THINKING_TIMEOUT_MILLIS)
    }

    /** [text] is about to be spoken. The repeat window starts properly at [replyFinished]; until
     * then the long timeout covers however long the speech takes. */
    fun replied(text: String) {
        lastReply = text
        DeviceLayers.clear(DeviceLayers.Slot.INTERACTION)
        enter(Mode.REPLIED, THINKING_TIMEOUT_MILLIS, text)
    }

    /** [text] - a question presses can answer - is about to be spoken. As [replied], with the
     * device in CONFIRM (its own "question" light and buzz) until the answer window runs out. */
    fun asked(text: String, question: DeviceButtonPolicy.Question) {
        lastReply = text
        DeviceLayers.clear(DeviceLayers.Slot.INTERACTION)
        enter(Mode.CONFIRM, THINKING_TIMEOUT_MILLIS, text, question)
    }

    /** A question asked on the phone's screen (ChatViewModel), answerable from the device as well
     * as by its buttons. Its answer window starts now: the screen shows the question at once. */
    fun askedOnScreen(text: String, question: DeviceButtonPolicy.Question) {
        asked(text, question)
        replyFinished()
    }

    /** The phone's screen sent a turn - a tapped option, or anything else. A question the device
     * was holding open is over: a press mustn't answer it a second time. */
    fun screenTurnSent() {
        if (synchronized(lock) { current.mode } == Mode.CONFIRM) idle()
    }

    /** Speech ended (or failed) - the repeat or answer window runs from now. No-op unless still
     * REPLIED or CONFIRM: a repeat started from idle doesn't open one. */
    fun replyFinished() {
        val state = synchronized(lock) { current.takeIf { it.mode == Mode.REPLIED || it.mode == Mode.CONFIRM } } ?: return
        val window = if (state.mode == Mode.CONFIRM) ANSWER_WINDOW_MILLIS else REPLY_WINDOW_MILLIS
        // Same mode again, so a fresh token: a press made in the old one is still found in
        // [recent] and means the same thing.
        enter(state.mode, window, state.replyText, state.question)
    }

    private fun idle() = enter(Mode.IDLE, null, null)

    private fun enter(mode: Mode, durationMillis: Long?, replyText: String?, question: DeviceButtonPolicy.Question? = null) {
        val now = System.currentTimeMillis()
        val state = synchronized(lock) {
            lastToken = lastToken % 255 + 1 // 1-255; 0 means "no mode" on the wire
            val next = ModeState(mode, lastToken, durationMillis?.let { now + it }, replyText, question)
            recent.addFirst(current)
            while (recent.size > RECENT_MODES) recent.removeLast()
            current = next
            next
        }
        sendMode(state, now)
    }

    /** The device starts every connection idle - catch it up if the phone isn't. */
    fun onConnected() {
        val now = System.currentTimeMillis()
        val state = synchronized(lock) { current }
        if (state.mode != Mode.IDLE && !state.expired(now)) sendMode(state, now)
    }

    private fun sendMode(state: ModeState, nowMillis: Long) {
        val timeoutSeconds = state.untilMillis?.let { (((it - nowMillis) + 999) / 1000).toInt().coerceAtLeast(1) } ?: 0
        NovaDeviceRepository.commandSender.value?.sendSetMode(state.mode.wire, state.token, timeoutSeconds)
    }

    /** Handles presses until cancelled - NovaDeviceService runs this for its lifetime. */
    suspend fun run(context: Context) {
        val app = context.applicationContext
        NovaDeviceRepository.events.filterIsInstance<NovaDeviceEvent.Press>().collect { press ->
            perform(app, decide(app, press))
        }
    }

    private suspend fun decide(context: Context, press: NovaDeviceEvent.Press): Action {
        val now = System.currentTimeMillis()
        val mode = synchronized(lock) {
            DeviceButtonPolicy.modeAt(current, recent.toList(), press.token, now)
        }
        val situation = DeviceButtonPolicy.Situation(
            mode = mode,
            departure = DeviceLayers.shown(DeviceLayers.Slot.DEPARTURE, now),
            firedReminders = firedReminders(context, now),
            idleActions = DevicePreferences.idleActions(context),
            lastReply = lastReply,
        )
        return DeviceButtonPolicy.resolve(situation, press.count)
    }

    /** The settings screen's "Try it": runs what [count] presses do when nothing else is going on,
     * as a press would - but never acts on a showing alert, which a real press would. */
    suspend fun tryIdleAction(context: Context, count: Int) {
        val app = context.applicationContext
        perform(app, DeviceButtonPolicy.idleAction(DevicePreferences.idleActions(app)[count], lastReply))
    }

    /** The same set the reminder LED pulses for (DeviceLayers.syncReminders). */
    private suspend fun firedReminders(context: Context, nowMillis: Long): List<DeviceButtonPolicy.FiredReminder> =
        NovaDatabase.getInstance(context).reminderDao().active()
            .filter { it.statusEnum == ReminderStatus.FIRED }
            .filter { (it.firedAtMillis ?: 0L) > nowMillis - DeviceLayers.REMINDER_GLOW_MILLIS }
            .map { DeviceButtonPolicy.FiredReminder(it.id, it.text) }

    private suspend fun perform(context: Context, action: Action) {
        when (action) {
            is Action.Repeat -> AssistVoiceService.repeat(context, action.text)
            Action.EndReply -> idle()
            Action.AcknowledgeDeparture -> {
                DeviceLayers.clear(DeviceLayers.Slot.DEPARTURE)
                AmbientNotifier.cancel(context)
                DepartureStore.clear(context)
                WidgetUpdater.requestUpdate(context)
            }
            is Action.ReadAloud -> {
                // Never through the phone speaker - the same rule ReminderSpeechService enforces;
                // checked here as well so the user feels the refusal instead of silence.
                val route = AudioRouteSignal.currentAudioRoute(context)
                if (!route.wiredHeadsetConnected && !route.bluetoothAudioConnected) {
                    DeviceCue.play(DeviceCue.Pattern.NOTHING)
                    return
                }
                ReminderSpeechService.speak(context, action.lines)
            }
            is Action.CompleteReminders -> action.ids.forEach { ReminderRepository.complete(context, it) }
            is Action.SnoozeReminders -> action.ids.forEach { ReminderRepository.snooze(context, it) }
            Action.Status -> {
                val upcoming = NovaDatabase.getInstance(context).reminderDao()
                    .scheduledDueBy(System.currentTimeMillis() + STATUS_HORIZON_MILLIS).size
                DeviceCue.playCount(upcoming)
                return // the count is the feedback
            }
            is Action.Ask -> {
                if (!AssistVoiceService.submitTranscript(context, action.instruction)) {
                    DeviceCue.play(DeviceCue.Pattern.NOTHING)
                    return
                }
            }
            is Action.Answer -> {
                // The same path as a spoken answer: the server continues the question's thread.
                if (!AssistVoiceService.submitTranscript(context, action.text)) {
                    DeviceCue.play(DeviceCue.Pattern.NOTHING)
                    return
                }
            }
            Action.Busy, Action.Nothing -> {
                DeviceCue.play(DeviceCue.Pattern.NOTHING)
                return
            }
        }
        DeviceCue.play(DeviceCue.Pattern.TICK)
    }

    /** Tests only - this is process-wide state. */
    internal fun reset() = synchronized(lock) {
        current = ModeState(Mode.IDLE, 0, null)
        recent.clear()
        lastToken = 0
        lastReply = null
    }

    internal fun currentMode(): ModeState = synchronized(lock) { current }
}
