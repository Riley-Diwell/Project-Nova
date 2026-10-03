package com.example.novav2.ble

import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.flow.take
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** [NovaDeviceRepository.events] delivers every press - a StateFlow would conflate repeats. */
class NovaDeviceRepositoryTest {
    @Test
    fun repeatedClicksEachEmit_heartbeatsAndBatteryDoNot() = runBlocking {
        val seen = mutableListOf<NovaDeviceEvent>()
        val job = launch(start = CoroutineStart.UNDISPATCHED) {
            NovaDeviceRepository.events.take(3).toList(seen)
        }

        NovaDeviceRepository.recordEvent(NovaDeviceEvent.Press(1))
        NovaDeviceRepository.recordEvent(NovaDeviceEvent.Heartbeat)
        NovaDeviceRepository.recordEvent(NovaDeviceEvent.Battery(50))
        NovaDeviceRepository.recordEvent(NovaDeviceEvent.Press(1))
        NovaDeviceRepository.recordEvent(NovaDeviceEvent.Press(3))
        job.join()

        assertEquals(
            listOf(NovaDeviceEvent.Press(1), NovaDeviceEvent.Press(1), NovaDeviceEvent.Press(3)),
            seen,
        )
    }

    @Test
    fun battery_isKeptWhileConnected_andClearedOnDisconnect() {
        NovaDeviceRepository.setConnectionState(NovaDeviceConnectionState.CONNECTED)
        NovaDeviceRepository.recordEvent(NovaDeviceEvent.Battery(72, millivolts = 3950))
        assertEquals(NovaDeviceEvent.Battery(72, millivolts = 3950), NovaDeviceRepository.battery.value)

        NovaDeviceRepository.setConnectionState(NovaDeviceConnectionState.DISCONNECTED)
        assertNull(NovaDeviceRepository.battery.value)
    }
}
