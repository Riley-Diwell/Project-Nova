package com.example.novav2.state

import com.example.novav2.ble.FakeCommandSender
import com.example.novav2.ble.NovaDeviceRepository
import com.example.novav2.ble.NovaLedLayer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

class DeviceLayersTest {
    private val sender = FakeCommandSender()
    private val t0 = 1_000_000L

    @Before
    fun setUp() {
        DeviceLayers.reset()
        NovaDeviceRepository.setCommandSender(sender)
    }

    @After
    fun tearDown() {
        NovaDeviceRepository.setCommandSender(null)
        DeviceLayers.reset()
    }

    private fun layers() = sender.sent.filterIsInstance<NovaLedLayer>()

    @Test
    fun show_sendsTheCueAsABlinkLayer() {
        DeviceLayers.show(DeviceLayers.Cue.LEAVE_SOON, 600_000L, t0)
        val layer = layers().single()
        assertEquals(DeviceLayers.Slot.DEPARTURE.id, layer.id)
        assertEquals(DeviceLayers.Rhythm.MEDIUM.periodMs, layer.periodMs)
        assertEquals(600, layer.timeoutSeconds)
    }

    @Test
    fun leaveSoonNeverDowngradesALiveLeaveNow() {
        DeviceLayers.show(DeviceLayers.Cue.LEAVE_NOW, 300_000L, t0)
        DeviceLayers.show(DeviceLayers.Cue.LEAVE_SOON, 600_000L, t0 + 60_000)
        assertEquals(listOf(DeviceLayers.Rhythm.FAST.periodMs), layers().map { it.periodMs })

        // Once leave-now has run out, the next trip's leave-soon shows again.
        DeviceLayers.show(DeviceLayers.Cue.LEAVE_SOON, 600_000L, t0 + 300_000)
        assertEquals(DeviceLayers.Rhythm.MEDIUM.periodMs, layers().last().periodMs)
    }

    @Test
    fun identicalShowIsNotResent() {
        DeviceLayers.show(DeviceLayers.Cue.REMINDER, 60_000L, t0)
        DeviceLayers.show(DeviceLayers.Cue.REMINDER, 60_000L, t0) // same expiry: reconcile re-running
        assertEquals(1, layers().size)
    }

    @Test
    fun clear_onlySendsForAShownSlot() {
        DeviceLayers.clear(DeviceLayers.Slot.REMINDER)
        assertEquals(emptyList<Any>(), sender.sent)
        DeviceLayers.show(DeviceLayers.Cue.REMINDER, null, t0)
        DeviceLayers.clear(DeviceLayers.Slot.REMINDER)
        assertEquals("clear ${DeviceLayers.Slot.REMINDER.id}", sender.sent.last())
    }

    @Test
    fun wireLayer_roundsTimeoutUp_nullOnceExpired_zeroForUntilCleared() {
        assertEquals(2, DeviceLayers.wireLayer(DeviceLayers.Cue.REMINDER, t0 + 1_001, t0)?.timeoutSeconds)
        assertNull(DeviceLayers.wireLayer(DeviceLayers.Cue.REMINDER, t0, t0))
        assertEquals(0, DeviceLayers.wireLayer(DeviceLayers.Cue.REMINDER, null, t0)?.timeoutSeconds)
    }

    @Test
    fun mix_reachesEveryDefaultColour() {
        // The slider must be able to show what each cue looks like before the user touches it.
        DeviceLayers.Cue.entries.forEach { cue ->
            val (red, green) = DeviceLayers.Mix.toColour(DeviceLayers.Mix.of(cue.red, cue.green))
            assertTrue(
                "$cue: ${cue.red}/${cue.green} came back as $red/$green",
                kotlin.math.abs(red - cue.red) <= 3 && kotlin.math.abs(green - cue.green) <= 3,
                )
        }
        assertEquals(Pair(0, 255), DeviceLayers.Mix.toColour(0))
        assertEquals(Pair(255, 255), DeviceLayers.Mix.toColour(50))
        assertEquals(Pair(255, 0), DeviceLayers.Mix.toColour(100))
    }

    @Test
    fun colourOverride_isWhatGoesOnTheWire_andResendShowsItNow() {
        DeviceLayers.show(DeviceLayers.Cue.REMINDER, null, t0)
        DeviceLayers.setColours(mapOf(DeviceLayers.Cue.REMINDER to 0))
        DeviceLayers.resend(t0)
        val layer = layers().last()
        assertEquals(DeviceLayers.Slot.REMINDER.id, layer.id)
        assertEquals(0 to 255, layer.red to layer.green)
        // Cues the user didn't touch keep their defaults.
        val leave = DeviceLayers.wireLayer(DeviceLayers.Cue.LEAVE_NOW, null, t0)!!
        assertEquals(255 to 0, leave.red to leave.green)
    }

    @Test
    fun preview_usesItsOwnSlot_andLeavesTheRealAlertAlone() {
        DeviceLayers.show(DeviceLayers.Cue.LEAVE_NOW, 300_000L, t0)
        DeviceLayers.preview(DeviceLayers.Cue.LEAVE_SOON, t0)
        val preview = layers().last()
        assertEquals(DeviceLayers.Slot.PREVIEW.id, preview.id)
        assertEquals(DeviceLayers.Rhythm.MEDIUM.periodMs, preview.periodMs)
        assertEquals(5, preview.timeoutSeconds)
        assertEquals(DeviceLayers.Cue.LEAVE_NOW, DeviceLayers.shown(DeviceLayers.Slot.DEPARTURE, t0)?.cue)
        assertNull(DeviceLayers.shown(DeviceLayers.Slot.PREVIEW, t0))
    }

    @Test
    fun deviceCue_isOneHapticCommand() {
        DeviceCue.play(DeviceCue.Pattern.REMINDER)
        assertEquals(listOf<Any>(listOf(250, 180, 250)), sender.sent)
    }
}
