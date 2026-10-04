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
 *   Nova asked yes or no          repeat it         yes               no
 *   Nova asked you to pick        repeat it         option 1          option 2  (4 = option 3)
 *   Nova just replied             repeat it         done with it      -
 *   "leave soon/now" showing      got it            got it            read it aloud
 *   a reminder went unanswered    done              snooze            read it aloud
 *   otherwise                     the user's idle actions (DevicePreferences)
 *
 * The alerts follow the LED: departure outranks a reminder there too, so a press always acts on
 * the thing the user can see. More presses than a row lists do nothing.
 *
 * One press means "repeat" after anything Nova says, questions included (Riley's call,
 * 2026-10-03): the two moments feel alike, and a habitual single press must never say yes to a
 * delete. So answers start at two presses, and a question has at most three of them. An open
 * question - no fixed answers - is just a reply: one press repeats it, hold to answer.
 */
object DeviceButtonPolicy {
    enum class Mode(val wire: Int) {
        IDLE(NovaBleProtocol.DeviceMode.IDLE),
        THINKING(NovaBleProtocol.DeviceMode.THINKING),
        REPLIED(NovaBleProtocol.DeviceMode.REPLIED),
        /** Nova asked a [Question] presses can answer. */
        CONFIRM(NovaBleProtocol.DeviceMode.CONFIRM),
    }

    /** A question presses can answer: [answers] in order, the first at [FIRST_ANSWER_PRESSES].
     * Each is sent to Nova word for word, as if spoken - for a choice, the option's own label,
     * which is what the server expects back (ask_choice). */
    data class Question(val answers: List<String>) {
        init {
            require(answers.size in 2..MAX_ANSWERS) { "a question needs 2-$MAX_ANSWERS answers" }
        }
    }

    /** One press repeats; see the class comment. */
    const val FIRST_ANSWER_PRESSES = 2
    /** Four presses is as many as anyone counts reliably. */
    const val MAX_ANSWERS = 3

    /** The question a turn's EventOut.confirmation/options leave for presses, or null when
     * presses can't answer it ("open", or nothing asked) - the reply rules apply then. */
    fun question(confirmation: String?, options: List<String>?): Question? = when (confirmation) {
        "yes_no" -> Question(listOf("Yes", "No"))
        "choice" -> options?.filter { it.isNotBlank() }?.take(MAX_ANSWERS)
            ?.takeIf { it.size >= 2 }?.let(::Question)
        else -> null
    }

    /** Spoken after the question when the device is connected - built here from [question],
     * never by the model, so it always matches what the presses do. */
    fun howToAnswer(question: Question): String =
        question.answers.mapIndexed { i, answer -> "$answer: press ${times(i + FIRST_ANSWER_PRESSES)}." }
            .joinToString(" ")

    private fun times(n: Int) = when (n) {
        1 -> "once"
        2 -> "twice"
        else -> "$n times"
    }

    /** One mode the phone put the device in. [token] (1-255) comes back with presses made in it. */
    data class ModeState(
        val mode: Mode,
        val token: Int,
        val untilMillis: Long?,
        val replyText: String? = null,
        /** Set in [Mode.CONFIRM]. */
        val question: Question? = null,
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
        /** Answer the question Nova asked with [text], as if it had been spoken. */
        data class Answer(val text: String) : Action
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
            Mode.CONFIRM -> when (count) {
                1 -> mode.replyText?.let(Action::Repeat) ?: Action.Nothing
                else -> mode.question?.answers?.getOrNull(count - FIRST_ANSWER_PRESSES)
                    ?.let(Action::Answer) ?: Action.Nothing
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
        idleAction(situation.idleActions[count], situation.lastReply)

    /** What one idle action does, alerts aside - also the settings screen's "Try it", which must
     * not complete a pulsing reminder the way a real press would. */
    fun idleAction(action: IdleAction?, lastReply: String?): Action =
        when (action) {
            null -> Action.Nothing
            is IdleAction.Custom -> Action.Ask(action.instruction)
            is IdleAction.Use -> when (action.preset) {
                IdleAction.Preset.NONE -> Action.Nothing
                IdleAction.Preset.STATUS -> Action.Status
                IdleAction.Preset.REPEAT_LAST_REPLY -> lastReply?.let(Action::Repeat) ?: Action.Nothing
                IdleAction.Preset.WHATS_NEXT, IdleAction.Preset.WHEN_TO_LEAVE ->
                    action.preset.prompt?.let(Action::Ask) ?: Action.Nothing
            }
        }

    /** One row of the settings screen's button guide: [presses] is what 1, 2 and 3 presses do. */
    data class GuideRow(val situation: String, val presses: List<String>)

    /**
     * The table in this object's comment, generated by running [resolve] on an example of each
     * situation - so the guide is the behaviour, and can't drift from it. The idle row names the
     * user's own choices rather than the actions they resolve to.
     */
    fun guide(idleActions: Map<Int, IdleAction>): List<GuideRow> =
        guideSituations(idleActions).map { (label, situation) ->
            GuideRow(label, PRESS_COUNTS.map { describe(resolve(situation, it)) })
        } + GuideRow(IDLE_GUIDE_LABEL, PRESS_COUNTS.map { count ->
            idleActions[count]?.let(::describe) ?: describe(Action.Nothing)
        })

    private val PRESS_COUNTS = 1..3
    private const val IDLE_GUIDE_LABEL = "Any other time"

    /** An example of every row of the table above the idle one, in the table's order. */
    internal fun guideSituations(idleActions: Map<Int, IdleAction>): List<Pair<String, Situation>> {
        val idle = ModeState(Mode.IDLE, 0, null)
        fun s(mode: ModeState, departure: DeviceLayers.Shown? = null, fired: List<FiredReminder> = emptyList()) =
            Situation(mode, departure, fired, idleActions, lastReply = "example")
        return listOf(
            "While Nova is thinking" to s(ModeState(Mode.THINKING, 1, null)),
            "Just after Nova replies" to s(ModeState(Mode.REPLIED, 1, null, "example")),
            "While the light says leave soon or now" to
                s(idle, departure = DeviceLayers.Shown(DeviceLayers.Cue.LEAVE_NOW, "example")),
            "After a reminder goes off" to s(idle, fired = listOf(FiredReminder("example", "example"))),
            "When Nova asks yes or no" to s(ModeState(Mode.CONFIRM, 1, null, "example", Question(listOf("Yes", "No")))),
            "When Nova asks you to pick" to s(ModeState(
                Mode.CONFIRM, 1, null, "example",
                Question(listOf("the first option", "the second option", "the third option")),
            )),
        )
    }

    /** Exhaustive on purpose: a new [Action] doesn't compile until the guide can name it. */
    internal fun describe(action: Action): String = when (action) {
        is Action.Repeat -> "Repeat it"
        Action.EndReply -> "Done with it"
        Action.AcknowledgeDeparture -> "Got it"
        is Action.ReadAloud -> "Read it aloud"
        is Action.CompleteReminders -> "Mark it done"
        is Action.SnoozeReminders -> "Snooze it"
        Action.Status -> IdleAction.Preset.STATUS.label
        is Action.Ask -> "Ask Nova"
        is Action.Answer -> "Answer: ${action.text}"
        Action.Busy -> "Busy buzz"
        Action.Nothing -> "Nothing"
    }

    fun describe(action: IdleAction): String = when (action) {
        is IdleAction.Use -> action.preset.label
        is IdleAction.Custom -> "“${action.instruction}”"
    }
}
