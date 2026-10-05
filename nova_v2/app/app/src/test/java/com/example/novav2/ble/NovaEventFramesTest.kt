package com.example.novav2.ble

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NovaEventFramesTest {
    private fun bytes(vararg values: Int) = values.map { it.toByte() }.toByteArray()

    @Test
    fun press_carriesCountModeAndToken() {
        assertEquals(NovaDeviceEvent.Press(2, mode = 2, token = 200), NovaEventFrames.parse(bytes(0x06, 2, 2, 200)))
        assertNull(NovaEventFrames.parse(bytes(0x06))) // no count
    }

    @Test
    fun olderFirmwareClicks_becomePressesWithoutAToken() {
        assertEquals(NovaDeviceEvent.Press(1), NovaEventFrames.parse(bytes(0x01)))
        assertEquals(NovaDeviceEvent.Press(2), NovaEventFrames.parse(bytes(0x02)))
        assertEquals(NovaDeviceEvent.Press(4), NovaEventFrames.parse(bytes(0x03, 4)))
    }

    @Test
    fun clearHeading() {
        assertEquals(NovaDeviceEvent.ClearHeading, NovaEventFrames.parse(bytes(0x07)))
    }

    @Test
    fun heartbeatAndUnknown() {
        assertEquals(NovaDeviceEvent.Heartbeat, NovaEventFrames.parse(bytes(0x05)))
        assertNull(NovaEventFrames.parse(bytes(0x7F)))
        assertNull(NovaEventFrames.parse(ByteArray(0)))
    }

    @Test
    fun battery_carriesPercentAndMillivolts() {
        // 3912 mV = 0x0F48, little-endian
        assertEquals(NovaDeviceEvent.Battery(64, millivolts = 3912), NovaEventFrames.parse(bytes(0x04, 64, 0x48, 0x0F)))
        assertEquals(NovaDeviceEvent.Battery(80), NovaEventFrames.parse(bytes(0x04, 80))) // percent-only firmware
        assertEquals(100, (NovaEventFrames.parse(bytes(0x04, 250)) as NovaDeviceEvent.Battery).percent)
        assertNull(NovaEventFrames.parse(bytes(0x04))) // no percent
    }

    @Test
    fun setModeFrame() {
        assertArrayEquals(bytes(0x07, 2, 9, 15, 0), NovaCommandFrames.setMode(2, 9, 15))
    }
}
