package com.example.novav2.ble

/**
 * One ambient LED layer as the firmware plays it (docs/ble-protocol.md "LED layers"). The device
 * shows only its highest-[priority] layer, and drops a layer by itself after [timeoutSeconds]
 * (0 = until cleared or the link drops).
 */
data class NovaLedLayer(
    val id: Int,
    val priority: Int,
    val red: Int,
    val green: Int,
    val pattern: Int,
    val periodMs: Int,
    val onMs: Int,
    val timeoutSeconds: Int,
)

/**
 * Byte layouts for the commands characteristic - kept apart from [NovaGattClient] so they can be
 * unit-tested without Bluetooth. Every field is clamped into its wire range rather than rejected,
 * the same stance as the original pulse commands.
 */
object NovaCommandFrames {
    fun pulse(type: Int, durationMs: Int): ByteArray =
        byteArrayOf(type.toByte(), tenMs(durationMs, max = 0xFF).toByte())

    fun ping(): ByteArray = byteArrayOf(NovaBleProtocol.CommandType.PING.toByte())

    /** type, id, priority, red, green, pattern, period x10ms (u16 LE), on-time x10ms,
     * timeout seconds (u16 LE) - 11 bytes. */
    fun setLayer(layer: NovaLedLayer): ByteArray {
        val period = tenMs(layer.periodMs, max = 0xFFFF)
        val timeout = layer.timeoutSeconds.coerceIn(0, 0xFFFF)
        return byteArrayOf(
            NovaBleProtocol.CommandType.SET_LAYER.toByte(),
            byte(layer.id), byte(layer.priority), byte(layer.red), byte(layer.green), byte(layer.pattern),
            (period and 0xFF).toByte(), (period shr 8).toByte(),
            tenMs(layer.onMs, max = 0xFF).toByte(),
            (timeout and 0xFF).toByte(), (timeout shr 8).toByte(),
        )
    }

    fun clearLayer(id: Int): ByteArray =
        byteArrayOf(NovaBleProtocol.CommandType.CLEAR_LAYER.toByte(), byte(id))

    /** Alternating on/off durations starting with on; anything past
     * [NovaBleProtocol.MAX_HAPTIC_STEPS] is dropped - the firmware's player holds no more. */
    fun playHaptic(stepsMs: List<Int>): ByteArray =
        byteArrayOf(NovaBleProtocol.CommandType.PLAY_HAPTIC.toByte()) +
            stepsMs.take(NovaBleProtocol.MAX_HAPTIC_STEPS).map { tenMs(it, max = 0xFF).toByte() }

    /** type, mode, token, timeout seconds (u16 LE, 0 = until changed) - 5 bytes. */
    fun setMode(mode: Int, token: Int, timeoutSeconds: Int): ByteArray {
        val timeout = timeoutSeconds.coerceIn(0, 0xFFFF)
        return byteArrayOf(
            NovaBleProtocol.CommandType.SET_MODE.toByte(), byte(mode), byte(token),
            (timeout and 0xFF).toByte(), (timeout shr 8).toByte(),
        )
    }

    private fun tenMs(ms: Int, max: Int) = (ms / 10).coerceIn(0, max)
    private fun byte(value: Int) = value.coerceIn(0, 0xFF).toByte()
}
