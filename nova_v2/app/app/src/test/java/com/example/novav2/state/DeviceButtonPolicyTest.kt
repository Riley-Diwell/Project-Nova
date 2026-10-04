package com.example.novav2.state

import com.example.novav2.state.DeviceButtonPolicy.Action
import com.example.novav2.state.DeviceButtonPolicy.FiredReminder
import com.example.novav2.state.DeviceButtonPolicy.Mode
import com.example.novav2.state.DeviceButtonPolicy.ModeState
import com.example.novav2.state.DeviceButtonPolicy.Situation
import org.junit.Assert.assertEquals
import org.junit.Test

class DeviceButtonPolicyTest {
    private val idle = ModeState(Mode.IDLE, 0, null)
    private val departure = DeviceLayers.Shown(DeviceLayers.Cue.LEAVE_NOW, "Time to leave for the dentist")
    private val fired = listOf(FiredReminder("a", "take the bins out"), FiredReminder("b", "call Sam"))

    private fun situation(
        mode: ModeState = idle,
        departure: DeviceLayers.Shown? = null,
        fired: List<FiredReminder> = emptyList(),
        lastReply: String? = "It's 3pm.",
    ) = Situation(mode, departure, fired, DevicePreferences.DEFAULT_IDLE_ACTIONS, lastReply)

    @Test
    fun thinking_everyPressIsBusy() {
        val thinking = situation(ModeState(Mode.THINKING, 4, null))
        (1..3).forEach { assertEquals(Action.Busy, DeviceButtonPolicy.resolve(thinking, it)) }
    }

    @Test
    fun replied_oneRepeats_twoEnds_evenWithAlertsShowing() {
        val replied = situation(ModeState(Mode.REPLIED, 5, null, "Your bus is at 4."), departure, fired)
        assertEquals(Action.Repeat("Your bus is at 4."), DeviceButtonPolicy.resolve(replied, 1))
        assertEquals(Action.EndReply, DeviceButtonPolicy.resolve(replied, 2))
        assertEquals(Action.Nothing, DeviceButtonPolicy.resolve(replied, 3))
    }

    @Test
    fun departureOutranksReminders_likeTheLed() {
        val both = situation(departure = departure, fired = fired)
        assertEquals(Action.AcknowledgeDeparture, DeviceButtonPolicy.resolve(both, 1))
        assertEquals(Action.AcknowledgeDeparture, DeviceButtonPolicy.resolve(both, 2))
        assertEquals(Action.ReadAloud(listOf("Time to leave for the dentist")), DeviceButtonPolicy.resolve(both, 3))
    }

    @Test
    fun firedReminders_doneSnoozeRead() {
        val s = situation(fired = fired)
        assertEquals(Action.CompleteReminders(listOf("a", "b")), DeviceButtonPolicy.resolve(s, 1))
        assertEquals(Action.SnoozeReminders(listOf("a", "b")), DeviceButtonPolicy.resolve(s, 2))
        assertEquals(
            Action.ReadAloud(listOf("Reminder: take the bins out", "Reminder: call Sam")),
            DeviceButtonPolicy.resolve(s, 3),
        )
        assertEquals(Action.Nothing, DeviceButtonPolicy.resolve(s, 4))
    }

    @Test
    fun idle_runsTheUsersActions() {
        val s = situation()
        assertEquals(Action.Status, DeviceButtonPolicy.resolve(s, 1))
        assertEquals(Action.Ask("What's next for me today?"), DeviceButtonPolicy.resolve(s, 2))
        assertEquals(Action.Repeat("It's 3pm."), DeviceButtonPolicy.resolve(s, 3))
        assertEquals(Action.Nothing, DeviceButtonPolicy.resolve(s, 4))
        assertEquals(Action.Nothing, DeviceButtonPolicy.resolve(situation(lastReply = null), 3))

        val custom = s.copy(idleActions = mapOf(2 to IdleAction.Custom("Text Sam I'm on my way")))
        assertEquals(Action.Ask("Text Sam I'm on my way"), DeviceButtonPolicy.resolve(custom, 2))
        assertEquals(Action.Nothing, DeviceButtonPolicy.resolve(custom, 1))
    }

    @Test
    fun modeAt_aRecognisedTokenWins_evenAfterThePhoneMovedOn() {
        val replied = ModeState(Mode.REPLIED, 7, untilMillis = 1_000, replyText = "hi")
        val nowIdle = ModeState(Mode.IDLE, 8, null)
        // Pressed during the reply's window, arrived just after the phone went idle.
        assertEquals(replied, DeviceButtonPolicy.modeAt(nowIdle, listOf(replied), token = 7, nowMillis = 2_000))
    }

    @Test
    fun modeAt_unknownOrMissingToken_usesThePhonesUnexpiredMode() {
        val replied = ModeState(Mode.REPLIED, 7, untilMillis = 1_000, replyText = "hi")
        assertEquals(replied, DeviceButtonPolicy.modeAt(replied, emptyList(), token = null, nowMillis = 500))
        assertEquals(replied, DeviceButtonPolicy.modeAt(replied, emptyList(), token = 99, nowMillis = 500))
        assertEquals(Mode.IDLE, DeviceButtonPolicy.modeAt(replied, emptyList(), token = null, nowMillis = 1_000).mode)
        assertEquals(Mode.IDLE, DeviceButtonPolicy.modeAt(replied, emptyList(), token = 0, nowMillis = 1_000).mode)
    }

    @Test
    fun idleAction_storageRoundTrips_andRejectsJunk() {
        val actions = listOf(IdleAction.Use(IdleAction.Preset.WHEN_TO_LEAVE), IdleAction.Custom("Log a glass of water"))
        actions.forEach { assertEquals(it, IdleAction.decode(IdleAction.encode(it))) }
        assertEquals(null, IdleAction.decode("preset:NOT_A_PRESET"))
        assertEquals(null, IdleAction.decode("custom:   "))
        assertEquals(null, IdleAction.decode("garbage"))
        assertEquals(IdleAction.Custom("a: b"), IdleAction.decode("custom:a: b"))
    }

    @Test
    fun statusCount_buzzesPerItemUpToThree_longForNone() {
        assertEquals(listOf(500), DeviceCue.countSteps(0))
        assertEquals(listOf(120), DeviceCue.countSteps(1))
        assertEquals(listOf(120, 200, 120, 200, 120), DeviceCue.countSteps(7))
    }
}
