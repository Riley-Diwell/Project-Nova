package com.example.novav2.ble

/** Parses the events characteristic (docs/ble-protocol.md) - apart from [NovaGattClient] so it
 * can be unit-tested without Bluetooth. */
object NovaEventFrames {
    /** Null for an empty frame or an unknown type - firmware/app protocol drifted; drop rather
     * than guess. */
    fun parse(frame: ByteArray): NovaDeviceEvent? {
        if (frame.isEmpty()) return null
        fun byteAt(i: Int): Int? = frame.getOrNull(i)?.toInt()?.and(0xFF)
        return when (frame[0].toInt() and 0xFF) {
            NovaBleProtocol.EventType.PRESS ->
                NovaDeviceEvent.Press(count = byteAt(1) ?: return null, mode = byteAt(2), token = byteAt(3))
            // Firmware older than PRESS: no mode or token, so the phone reads it against its own.
            NovaBleProtocol.EventType.SINGLE_CLICK -> NovaDeviceEvent.Press(1)
            NovaBleProtocol.EventType.DOUBLE_CLICK -> NovaDeviceEvent.Press(2)
            NovaBleProtocol.EventType.MULTI_CLICK -> NovaDeviceEvent.Press(byteAt(1) ?: return null)
            NovaBleProtocol.EventType.BATTERY -> NovaDeviceEvent.Battery(
                percent = (byteAt(1) ?: return null).coerceAtMost(100),
                millivolts = byteAt(2)?.let { lo -> byteAt(3)?.let { hi -> lo or (hi shl 8) } },
            )
            NovaBleProtocol.EventType.HEARTBEAT -> NovaDeviceEvent.Heartbeat
            else -> null
        }
    }
}
