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
        // 0x01-0x03: single/double/multi click from firmware older than PRESS - still parsed.
        const val SINGLE_CLICK = 0x01 // no payload
        const val DOUBLE_CLICK = 0x02 // no payload
        const val MULTI_CLICK = 0x03  // payload: 1 byte click count
        const val BATTERY = 0x04      // payload: percent 0-100, then millivolts (2 bytes LE) -
                                       // older firmware sent percent alone
        const val HEARTBEAT = 0x05    // no payload
        const val PRESS = 0x06        // payload: count, mode, token (see SET_MODE)
    }

    /** commands characteristic (phone → device, Write no response): 1 type byte + payload. */
    object CommandType {
        const val HAPTIC_PULSE = 0x01 // payload: 1 byte duration, x10ms
        const val LED_PULSE = 0x02    // payload: 1 byte duration, x10ms
        const val PING = 0x03         // no payload — device replies with a heartbeat event
        const val SET_LAYER = 0x04    // payload: see NovaCommandFrames.setLayer
        const val CLEAR_LAYER = 0x05  // payload: 1 byte layer id, LAYER_ID_ALL for every layer
        const val PLAY_HAPTIC = 0x06  // payload: 1-6 on/off step durations, x10ms, starting on
        const val SET_MODE = 0x07     // payload: mode, token, timeout seconds (u16 LE, 0 = until changed)
    }

    /** SET_MODE's mode byte - echoed back on every PRESS (see DeviceInteraction). */
    object DeviceMode {
        const val IDLE = 0x00
        const val THINKING = 0x01
        const val REPLIED = 0x02
    }

    /** SET_LAYER's pattern byte. */
    object LedPattern {
        const val SOLID = 0x00
        const val BLINK = 0x01   // on for onMs at the start of every period
        const val BREATHE = 0x02 // fades up and down once per period
    }

    const val LAYER_ID_ALL = 0xFF
    const val MAX_HAPTIC_STEPS = 6

    /** audio characteristic envelope — byte 0 of every notification is a wrapping
     * sequence number (see [com.example.novav2.ble.AdpcmDecoder]), byte 1 is these
     * flags, bytes 2+ are the ADPCM block itself. */
    const val AUDIO_FLAG_START = 0x01
    const val AUDIO_FLAG_END = 0x02
    /** START frame only: a dictated note. Defined for the protocol; the firmware doesn't send it yet. */
    const val AUDIO_FLAG_NOTE = 0x04

    /** Samples per ADPCM block (firmware bufferLen) and the mic's rate - one block is 32 ms. */
    const val SAMPLES_PER_BLOCK = 512
    const val SAMPLE_RATE_HZ = 16_000
}
