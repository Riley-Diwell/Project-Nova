package com.example.novav2.ble

import java.util.UUID

/**
 * Fixed constants mirroring nova_v2/firmware/ESP32-S3.ino/ESP32-S3.ino.ino and
 * nova_v2/docs/ble-protocol.md — both sides hardcode the same UUIDs and byte
 * layouts to find each other's characteristics. Don't change either without
 * updating both.
 */
object NovaBleProtocol {
    val SERVICE_UUID: UUID = UUID.fromString("0f016870-7232-4454-8f07-c3f09eab3fcc")
    val EVENTS_CHARACTERISTIC_UUID: UUID = UUID.fromString("2d75cb8a-3dbe-441e-bc69-ba91ad698089")
    val AUDIO_CHARACTERISTIC_UUID: UUID = UUID.fromString("98a3d8ca-c54d-4266-8706-cf15447d2058")
    val COMMANDS_CHARACTERISTIC_UUID: UUID = UUID.fromString("660d7ca3-765e-41d2-8c5a-c282ce9cdf90")

    /** Standard BLE descriptor UUID for enabling notifications on a characteristic
     * (Client Characteristic Configuration Descriptor) — a fixed Bluetooth SIG
     * value, not Nova-specific, needed on every characteristic we subscribe to. */
    val CLIENT_CHARACTERISTIC_CONFIG_UUID: UUID =
        UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

    /** 262-byte audio notification (2-byte envelope + 260-byte ADPCM block) +
     * 3-byte ATT header. Must match firmware's NimBLEDevice::setMTU(265) — MTU
     * is negotiated between both sides, not unilaterally set by either alone. */
    const val PREFERRED_MTU = 265

    /** events characteristic (device → phone, Notify): 1 type byte + payload. */
    object EventType {
        const val SINGLE_CLICK = 0x01 // no payload
        const val DOUBLE_CLICK = 0x02 // no payload
        const val MULTI_CLICK = 0x03  // payload: 1 byte click count
        const val BATTERY = 0x04      // payload: 1 byte percent 0-100 — firmware
                                       // doesn't send this yet (no confirmed
                                       // battery-sense circuit), handled here anyway
        const val HEARTBEAT = 0x05    // no payload
    }

    /** commands characteristic (phone → device, Write no response): 1 type byte + payload. */
    object CommandType {
        const val HAPTIC_PULSE = 0x01 // payload: 1 byte duration, x10ms
        const val LED_PULSE = 0x02    // payload: 1 byte duration, x10ms
        const val PING = 0x03         // no payload — device replies with a heartbeat event
    }

    /** audio characteristic envelope — byte 0 of every notification is a wrapping
     * sequence number (see [com.example.novav2.ble.AdpcmDecoder]), byte 1 is these
     * flags, bytes 2+ are the ADPCM block itself. */
    const val AUDIO_FLAG_START = 0x01
    const val AUDIO_FLAG_END = 0x02
}
