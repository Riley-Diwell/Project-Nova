package com.example.novav2.state

import com.example.novav2.ble.FakeCommandSender
import com.example.novav2.ble.NovaBleProtocol
import com.example.novav2.ble.NovaDeviceRepository
import com.example.novav2.ble.NovaLedLayer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** The mode half of DeviceInteraction - what the device is told as a voice turn runs. */
class DeviceInteractionTest {
    private val sender = FakeCommandSender()

    @Before
    fun setUp() {
        DeviceInteraction.reset()
        DeviceLayers.reset()
        NovaDeviceRepository.setCommandSender(sender)
    }

    @After
    fun tearDown() {
        NovaDeviceRepository.setCommandSender(null)
        DeviceInteraction.reset()
        DeviceLayers.reset()
    }

    private fun modes() = sender.sent.filterIsInstance<String>().filter { it.startsWith("mode") }

    @Test
    fun aTurn_thinksThenRepliesThenOpensTheRepeatWindow() {
        DeviceInteraction.thinking()
        DeviceInteraction.replied("Your bus is at 4.")
        DeviceInteraction.replyFinished()

        val m = NovaBleProtocol.DeviceMode
        assertEquals(
            listOf("mode ${m.THINKING}/1/60", "mode ${m.REPLIED}/2/60", "mode ${m.REPLIED}/3/15"),
            modes(),
        )
        // The thinking breathe went on and came off again with the reply.
        assertEquals(DeviceLayers.Cue.THINKING.rhythm.periodMs, sender.sent.filterIsInstance<NovaLedLayer>().single().periodMs)
        assertEquals(
            listOf("mode ${NovaBleProtocol.DeviceMode.THINKING}/1/60", "clear ${DeviceLayers.Slot.INTERACTION.id}"),
            sender.sent.filterIsInstance<String>().take(2),
        )
        assertEquals("Your bus is at 4.", DeviceInteraction.lastReply)
    }

    @Test
    fun aQuestion_putsTheDeviceInConfirm_thenOpensTheAnswerWindow() {
        val question = DeviceButtonPolicy.Question(listOf("Yes", "No"))
        DeviceInteraction.thinking()
        DeviceInteraction.asked("Delete it? Yes: press twice. No: press 3 times.", question)
        DeviceInteraction.replyFinished()

        val m = NovaBleProtocol.DeviceMode
        assertEquals(
            listOf("mode ${m.THINKING}/1/60", "mode ${m.CONFIRM}/2/60", "mode ${m.CONFIRM}/3/60"),
            modes(),
        )
        val now = DeviceInteraction.currentMode()
        assertEquals(question, now.question)
        assertEquals("Delete it? Yes: press twice. No: press 3 times.", now.replyText)
        // Under the server's 3-minute hold on the question.
        assertTrue(DeviceInteraction.ANSWER_WINDOW_MILLIS < 3 * 60_000L)
    }

    @Test
    fun aQuestionOnScreen_opensTheAnswerWindowAtOnce_andASentTurnEndsIt() {
        val question = DeviceButtonPolicy.Question(listOf("The 1:30 one", "The 2:00 one"))
        DeviceInteraction.askedOnScreen("Which dentist reminder?", question)
        assertEquals(DeviceButtonPolicy.Mode.CONFIRM, DeviceInteraction.currentMode().mode)
        assertEquals(question, DeviceInteraction.currentMode().question)

        DeviceInteraction.screenTurnSent()
        val m = NovaBleProtocol.DeviceMode
        assertEquals(DeviceButtonPolicy.Mode.IDLE, DeviceInteraction.currentMode().mode)
        assertEquals("mode ${m.IDLE}/3/0", modes().last())
    }

    @Test
    fun screenTurnSent_outsideAQuestion_leavesTheModeAlone() {
        DeviceInteraction.replied("Your bus is at 4.")
        DeviceInteraction.screenTurnSent()
        assertEquals(DeviceButtonPolicy.Mode.REPLIED, DeviceInteraction.currentMode().mode)
    }

    @Test
    fun replyFinished_withoutAReply_doesNothing() {
        DeviceInteraction.replyFinished()
        assertEquals(emptyList<String>(), modes())
    }

    @Test
    fun tokensCycle1To255_neverZero() {
        repeat(256) { DeviceInteraction.thinking() }
        assertEquals(1, DeviceInteraction.currentMode().token)
    }
}
