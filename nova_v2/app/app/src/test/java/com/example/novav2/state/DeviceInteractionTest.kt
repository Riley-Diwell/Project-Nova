package com.example.novav2.state

import com.example.novav2.ble.FakeCommandSender
import com.example.novav2.ble.NovaBleProtocol
import com.example.novav2.ble.NovaDeviceRepository
import com.example.novav2.ble.NovaLedLayer
import org.junit.After
import org.junit.Assert.assertEquals
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
