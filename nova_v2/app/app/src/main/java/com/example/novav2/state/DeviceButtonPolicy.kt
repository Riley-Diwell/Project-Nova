package com.example.novav2.state

import com.example.novav2.ble.NovaBleProtocol

/**
 * What a press of the device button means - a pure function of the situation, like
 * [InterruptionPolicy]. [DeviceInteraction] gathers the situation and carries the result out.
 *
 * Holding the button always talks to Nova (or, after one tap, dictates a note); the firmware
 * handles that itself. Presses depend on the moment, first match wins:
 *
 *   situation                     1 press           2 presses         3 presses
 *   Nova is thinking              (busy buzz)       (busy buzz)       (busy buzz)
 *   Nova just replied             repeat it         done with it      -
 *   "leave soon/now" showing      got it            got it            read it aloud
 *   a reminder went unanswered    done              snooze            read it aloud
 *   otherwise                     the user's idle actions (DevicePreferences)
 *
 * The alerts follow the LED: departure outranks a reminder there too, so a press always acts on
 * the thing the user can see. More presses than a row lists do nothing.
 */
object DeviceButtonPolicy {
    enum class Mode(val wire: Int) {
        IDLE(NovaBleProtocol.DeviceMode.IDLE),
        THINKING(NovaBleProtocol.DeviceMode.THINKING),
        REPLIED(NovaBleProtocol.DeviceMode.REPLIED),
    }

    /** One mode the phone put the device in. [token] (1-255) comes back with presses made in it. */
    data class ModeState(
        val mode: Mode,
        val token: Int,
        val untilMillis: Long?,
        val replyText: String? = null,
    ) {
        fun expired(nowMillis: Long) = untilMillis != null && nowMillis >= untilMillis
    }

    data class FiredReminder(val id: String, val text: String)

    data class Situation(
        val mode: ModeState,
        /** The departure cue on the LED, if one is showing. */
        val departure: DeviceLayers.Shown?,
        /** Reminders that went off unanswered - what the reminder LED is pulsing for. */
        val firedReminders: List<FiredReminder>,
        val idleActions: Map<Int, IdleAction>,
        val lastReply: String?,
    )

    sealed interface Action {
        data class Repeat(val text: String) : Action
        data object EndReply : Action
        data object AcknowledgeDeparture : Action
        data class ReadAloud(val lines: List<String>) : Action
        data class CompleteReminders(val ids: List<String>) : Action
        data class SnoozeReminders(val ids: List<String>) : Action
        data object Status : Action
        /** Send [instruction] to Nova as if it had been spoken. */
        data class Ask(val instruction: String) : Action
        data object Busy : Action
        data object Nothing : Action
    }

    /**
     * The mode a press was made in. The device reports the token it had when the press sequence
     * began, which can lag the phone by the 500 ms multi-press window: a "repeat" pressed in the
     * last moment of a reply's window must still mean repeat, even if the phone has since moved
     * on. So a token the phone recognises wins; otherwise (old firmware, a token from a previous
     * connection) it is the phone's own current mode, unless that has run out.
     */
    fun modeAt(current: ModeState, recent: List<ModeState>, token: Int?, nowMillis: Long): ModeState {
        if (token != null && token != 0) {
            (listOf(current) + recent).firstOrNull { it.token == token }?.let { return it }
        }
        return if (current.expired(nowMillis)) ModeState(Mode.IDLE, 0, null) else current
    }

    fun resolve(situation: Situation, count: Int): Action {
        val mode = situation.mode
        return when (mode.mode) {
            Mode.THINKING -> Action.Busy
            Mode.REPLIED -> when (count) {
                1 -> mode.replyText?.let(Action::Repeat) ?: Action.Nothing
                2 -> Action.EndReply
                else -> Action.Nothing
            }
            Mode.IDLE -> alert(situation, count) ?: idle(situation, count)
        }
    }

    /** Null when nothing is alerting, so the press falls through to the idle actions. */
    private fun alert(situation: Situation, count: Int): Action? {
        situation.departure?.let { departure ->
            return when (count) {
                1, 2 -> Action.AcknowledgeDeparture
                3 -> departure.text?.let { Action.ReadAloud(listOf(it)) } ?: Action.Nothing
                else -> Action.Nothing
            }
        }
        val fired = situation.firedReminders.takeIf { it.isNotEmpty() } ?: return null
        return when (count) {
            1 -> Action.CompleteReminders(fired.map { it.id })
            2 -> Action.SnoozeReminders(fired.map { it.id })
            3 -> Action.ReadAloud(fired.map { "Reminder: ${it.text}" })
            else -> Action.Nothing
        }
    }

    private fun idle(situation: Situation, count: Int): Action =
        when (val action = situation.idleActions[count]) {
            null -> Action.Nothing
            is IdleAction.Custom -> Action.Ask(action.instruction)
            is IdleAction.Use -> when (action.preset) {
                IdleAction.Preset.NONE -> Action.Nothing
                IdleAction.Preset.STATUS -> Action.Status
                IdleAction.Preset.REPEAT_LAST_REPLY -> situation.lastReply?.let(Action::Repeat) ?: Action.Nothing
                IdleAction.Preset.WHATS_NEXT, IdleAction.Preset.WHEN_TO_LEAVE ->
                    action.preset.prompt?.let(Action::Ask) ?: Action.Nothing
            }
        }
}
