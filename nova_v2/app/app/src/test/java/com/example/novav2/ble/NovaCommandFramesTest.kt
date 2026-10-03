package com.example.novav2.ble

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

/** Byte layouts must match the firmware's handleCommand - see docs/ble-protocol.md. */
class NovaCommandFramesTest {
    private fun bytes(vararg values: Int) = values.map { it.toByte() }.toByteArray()

    @Test
    fun setLayer_layout() {
        val frame = NovaCommandFrames.setLayer(
            NovaLedLayer(
                id = 2, priority = 20, red = 255, green = 70, pattern = NovaBleProtocol.LedPattern.BLINK,
                periodMs = 3_000, onMs = 150, timeoutSeconds = 300,
            )
        )
        // period 300 x10ms = 0x012C, timeout 300 s = 0x012C, both little-endian
        assertArrayEquals(bytes(0x04, 2, 20, 255, 70, 0x01, 0x2C, 0x01, 15, 0x2C, 0x01), frame)
    }

    @Test
    fun setLayer_clampsIntoWireRange() {
        val frame = NovaCommandFrames.setLayer(
            NovaLedLayer(
                id = 999, priority = -1, red = 300, green = 0, pattern = 0,
                periodMs = 10_000_000, onMs = 9_999, timeoutSeconds = 100_000,
            )
        )
        assertArrayEquals(bytes(0x04, 255, 0, 255, 0, 0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF), frame)
    }

    @Test
    fun playHaptic_tenMsStepsCappedAtSix() {
        assertArrayEquals(bytes(0x06, 25, 18, 25), NovaCommandFrames.playHaptic(listOf(250, 180, 250)))
        assertEquals(1 + NovaBleProtocol.MAX_HAPTIC_STEPS, NovaCommandFrames.playHaptic(List(9) { 100 }).size)
    }

    @Test
    fun pulseClearAndPing() {
        assertArrayEquals(bytes(0x01, 20), NovaCommandFrames.pulse(NovaBleProtocol.CommandType.HAPTIC_PULSE, 200))
        assertArrayEquals(bytes(0x02, 255), NovaCommandFrames.pulse(NovaBleProtocol.CommandType.LED_PULSE, 60_000))
        assertArrayEquals(bytes(0x05, 0xFF), NovaCommandFrames.clearLayer(NovaBleProtocol.LAYER_ID_ALL))
        assertArrayEquals(bytes(0x03), NovaCommandFrames.ping())
    }
}
