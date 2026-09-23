package com.example.novav2.ble

import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.content.Context
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow

/**
 * Owns one GATT connection to a paired Nova device, following
 * nova_v2/docs/ble-protocol.md. [com.example.novav2.service.NovaDeviceService] is the
 * only intended owner of an instance — this class does not persist which device to
 * connect to (see [NovaDevicePairing]) or decide when to reconnect, it just is the
 * connection once told to open one.
 *
 * GATT operation sequencing: `BluetoothGatt` only allows one outstanding
 * request (MTU/discovery/descriptor write/characteristic write) at a time per
 * connection — issuing a second before the first's callback fires silently
 * fails or is dropped. [enqueue] serialises requestMtu → discoverServices →
 * enable-notify(events) → enable-notify(audio) → any subsequent command
 * writes through a tiny internal queue rather than firing them concurrently.
 */
class NovaGattClient(
    private val context: Context,
    private val listener: Listener,
) : NovaCommandSender {

    interface Listener {
        fun onConnectionStateChanged(state: NovaDeviceConnectionState)
    }

    private var gatt: BluetoothGatt? = null
    private var eventsCharacteristic: BluetoothGattCharacteristic? = null
    private var audioCharacteristic: BluetoothGattCharacteristic? = null
    private var commandsCharacteristic: BluetoothGattCharacteristic? = null

    private val audioDecoder = AdpcmDecoder()
    private var expectedAudioSeq: Int? = null

    private val _audioChunks = MutableSharedFlow<ShortArray>(extraBufferCapacity = 64)
    val audioChunks: SharedFlow<ShortArray> = _audioChunks.asSharedFlow()

    private val _utteranceActive = MutableStateFlow(false)
    val utteranceActive: StateFlow<Boolean> = _utteranceActive

    // Reassembles one whole utterance's PCM16 little-endian samples across every
    // block between the BLE start/end flags, for completedUtterances below.
    // Buffered here rather than by a collector re-joining audioChunks/utteranceActive
    // on the app side - both of those are separate Flows with no ordering guarantee
    // between them, whereas handleAudioFrame already sees every block for one
    // utterance serially on the same BLE callback thread.
    private val utteranceBuffer = java.io.ByteArrayOutputStream()

    private val _completedUtterances = MutableSharedFlow<ByteArray>(extraBufferCapacity = 4)
    /** One full utterance's PCM16 little-endian mono samples at 16kHz, emitted once
     * the end-of-utterance flag arrives - for callers (VoskTranscriber via
     * NovaDeviceService) that need the whole recording rather than [audioChunks]'s
     * live per-block feed. */
    val completedUtterances: SharedFlow<ByteArray> = _completedUtterances.asSharedFlow()

    // --- tiny sequential operation queue (see class doc comment) ---
    private val pendingOperations = ArrayDeque<() -> Unit>()
    private var operationInFlight = false

    private fun enqueue(operation: () -> Unit) {
        synchronized(pendingOperations) {
            pendingOperations.addLast(operation)
        }
        runNextIfIdle()
    }

    private fun runNextIfIdle() {
        val next: (() -> Unit)?
        synchronized(pendingOperations) {
            if (operationInFlight) return
            next = pendingOperations.removeFirstOrNull()
            if (next != null) operationInFlight = true
        }
        next?.invoke()
    }

    private fun completeOperation() {
        synchronized(pendingOperations) { operationInFlight = false }
        runNextIfIdle()
    }

    /**
     * Drops whatever is queued and clears the in-flight flag. Required on every
     * disconnect, not just an explicit [disconnect] call: with `autoConnect =
     * true`, Android reconnects using the *same* [BluetoothGatt]/callback and
     * [NovaDeviceService] reuses this same client instance rather than building
     * a fresh one, so a drop that happens mid-sequence (e.g. between
     * `requestMtu` and its `onMtuChanged` callback, which now never fires) would
     * otherwise leave [operationInFlight] stuck `true` forever - silently
     * deadlocking the queue so the next reconnect's own `requestMtu` enqueues
     * behind a flag nothing will ever clear.
     */
    private fun resetQueue() {
        synchronized(pendingOperations) {
            pendingOperations.clear()
            operationInFlight = false
        }
    }

    /**
     * Opens (or resumes) a connection to [address]. [autoConnect] true is
     * Android's background/whitelist connect mode — slower to establish but
     * reconnects automatically whenever the device comes back in range, which
     * is what "stays connected like AirPods" needs for
     * [com.example.novav2.service.NovaDeviceService]'s reconnect path. A
     * user-initiated "pair now" action would want false instead (fast, direct
     * connect) — left as a caller decision, not hardcoded here.
     */
    fun connect(address: String, autoConnect: Boolean) {
        val adapter = (context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager).adapter
        val device: BluetoothDevice = adapter.getRemoteDevice(address)
        listener.onConnectionStateChanged(NovaDeviceConnectionState.CONNECTING)
        gatt = device.connectGatt(context, autoConnect, gattCallback, BluetoothDevice.TRANSPORT_LE)
    }

    fun disconnect() {
        gatt?.disconnect()
        gatt?.close()
        gatt = null
        eventsCharacteristic = null
        audioCharacteristic = null
        commandsCharacteristic = null
        resetQueue()
        listener.onConnectionStateChanged(NovaDeviceConnectionState.DISCONNECTED)
    }

    override fun sendHapticPulse(durationMs: Int) =
        sendCommand(NovaBleProtocol.CommandType.HAPTIC_PULSE, durationMs)

    override fun sendLedPulse(durationMs: Int) =
        sendCommand(NovaBleProtocol.CommandType.LED_PULSE, durationMs)

    /** [durationMs] is clamped into the 1-byte, x10ms unit the firmware expects
     * (see docs/ble-protocol.md) — 0..2550ms, silently clamped rather than
     * rejected, since a haptic/LED nudge has no reason to need finer range.
     *
     * Goes through [enqueue] like every other GATT operation on this connection
     * — `WRITE_TYPE_NO_RESPONSE` skips the remote ATT response, but Android's
     * own `BluetoothGatt` still only allows one outstanding local write at a
     * time regardless of write type; firing a second write before the first's
     * `onCharacteristicWrite` callback silently drops it (confirmed against
     * real hardware: sending haptic then LED back-to-back only ever delivered
     * the haptic write). */
    private fun sendCommand(type: Int, durationMs: Int) {
        val characteristic = commandsCharacteristic ?: return
        val g = gatt ?: return
        val tenMsUnits = (durationMs / 10).coerceIn(0, 255)
        val payload = byteArrayOf(type.toByte(), tenMsUnits.toByte())
        enqueue {
            characteristic.writeType = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
            @Suppress("DEPRECATION") // classic .value + writeCharacteristic(characteristic)
            // works across API 24-36; the API 33 byte-array overload is additive,
            // not a required replacement, and picking one path keeps this readable.
            characteristic.value = payload
            @Suppress("DEPRECATION")
            g.writeCharacteristic(characteristic)
            // onCharacteristicWrite completes this queue entry.
        }
    }

    /** Called periodically by [com.example.novav2.service.NovaDeviceService] while
     * connected — proof-of-life for the firmware's own stale-connection watchdog
     * (see docs/ble-protocol.md "Connection liveness"), since a BLE link stays up
     * at the radio level even after this app's process dies, so the firmware can't
     * tell "phone gone quiet" from "phone gone" any other way. */
    fun sendPing() = sendCommand(NovaBleProtocol.CommandType.PING, 0)

    private val gattCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    // First queued op after connect — discoverServices() and
                    // everything after it waits for this callback, same
                    // one-at-a-time rule as the rest of the queue.
                    enqueue { g.requestMtu(NovaBleProtocol.PREFERRED_MTU) }
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    // See resetQueue()'s doc comment - autoConnect reuses this same
                    // instance for the next connection attempt, so anything left
                    // in-flight from this one must not block it.
                    resetQueue()
                    listener.onConnectionStateChanged(NovaDeviceConnectionState.DISCONNECTED)
                }
            }
        }

        override fun onMtuChanged(g: BluetoothGatt, mtu: Int, status: Int) {
            completeOperation()
            enqueue { g.discoverServices() }
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            completeOperation()
            val service = g.getService(NovaBleProtocol.SERVICE_UUID)
            if (service == null) {
                // Wrong device, or firmware not yet flashed with this profile —
                // nothing recoverable from here; surface as disconnected rather
                // than a connected-but-broken state the rest of the app has to
                // special-case.
                listener.onConnectionStateChanged(NovaDeviceConnectionState.DISCONNECTED)
                return
            }

            eventsCharacteristic = service.getCharacteristic(NovaBleProtocol.EVENTS_CHARACTERISTIC_UUID)
            audioCharacteristic = service.getCharacteristic(NovaBleProtocol.AUDIO_CHARACTERISTIC_UUID)
            commandsCharacteristic = service.getCharacteristic(NovaBleProtocol.COMMANDS_CHARACTERISTIC_UUID)

            eventsCharacteristic?.let { enqueueEnableNotify(g, it) }
            audioCharacteristic?.let { enqueueEnableNotify(g, it) }

            // Only announce CONNECTED once the queue above has actually drained,
            // not just been queued - NovaDeviceRepository.commandSender goes
            // non-null on this callback, and a command write issued while a
            // notify-enable descriptor write is still in flight would collide
            // with it and get silently dropped (see class doc comment).
            enqueue {
                listener.onConnectionStateChanged(NovaDeviceConnectionState.CONNECTED)
                completeOperation()
            }
        }

        override fun onDescriptorWrite(g: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
            completeOperation()
        }

        override fun onCharacteristicWrite(g: BluetoothGatt, characteristic: BluetoothGattCharacteristic, status: Int) {
            completeOperation()
        }

        @Suppress("DEPRECATION") // see the non-deprecated onCharacteristicChanged(gatt,
        // characteristic, value) overload added in API 33 — not overridden here so
        // there's one code path across API levels; this deprecated form is still
        // invoked on every version as long as the new one isn't also overridden.
        override fun onCharacteristicChanged(g: BluetoothGatt, characteristic: BluetoothGattCharacteristic) {
            val value = characteristic.value ?: return
            when (characteristic.uuid) {
                NovaBleProtocol.EVENTS_CHARACTERISTIC_UUID -> handleEventFrame(value)
                NovaBleProtocol.AUDIO_CHARACTERISTIC_UUID -> handleAudioFrame(value)
            }
        }
    }

    private fun enqueueEnableNotify(g: BluetoothGatt, characteristic: BluetoothGattCharacteristic) {
        enqueue {
            g.setCharacteristicNotification(characteristic, true)
            val cccd = characteristic.getDescriptor(NovaBleProtocol.CLIENT_CHARACTERISTIC_CONFIG_UUID)
            if (cccd == null) {
                completeOperation()
                return@enqueue
            }
            @Suppress("DEPRECATION")
            cccd.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
            @Suppress("DEPRECATION")
            g.writeDescriptor(cccd)
            // onDescriptorWrite completes this queue entry — setCharacteristicNotification
            // itself is a local call with no callback, it's the descriptor write that
            // actually talks to the device and needs sequencing against the next op.
        }
    }

    private fun handleEventFrame(frame: ByteArray) {
        if (frame.isEmpty()) return
        val type = frame[0].toInt() and 0xFF
        val event = when (type) {
            NovaBleProtocol.EventType.SINGLE_CLICK -> NovaDeviceEvent.SingleClick
            NovaBleProtocol.EventType.DOUBLE_CLICK -> NovaDeviceEvent.DoubleClick
            NovaBleProtocol.EventType.MULTI_CLICK ->
                NovaDeviceEvent.MultiClick(count = frame.getOrNull(1)?.toInt()?.and(0xFF) ?: 0)
            NovaBleProtocol.EventType.BATTERY ->
                NovaDeviceEvent.Battery(percent = frame.getOrNull(1)?.toInt()?.and(0xFF) ?: 0)
            NovaBleProtocol.EventType.HEARTBEAT -> NovaDeviceEvent.Heartbeat
            else -> return // unknown type - firmware/app protocol drifted; drop rather than guess
        }
        NovaDeviceRepository.recordEvent(event)
    }

    private fun handleAudioFrame(frame: ByteArray) {
        if (frame.size < 2) return
        val seq = frame[0].toInt() and 0xFF
        val flags = frame[1].toInt() and 0xFF
        val block = frame.copyOfRange(2, frame.size)

        if (flags and NovaBleProtocol.AUDIO_FLAG_START != 0) {
            audioDecoder.reset()
            expectedAudioSeq = seq
            _utteranceActive.value = true
            utteranceBuffer.reset()
        }

        val expected = expectedAudioSeq
        if (expected != null && expected != seq) {
            // A notification was dropped somewhere between firmware and here -
            // this is exactly what the sequence number exists to catch (see
            // docs/ble-protocol.md). Nothing to recover mid-utterance; just log
            // and keep decoding what does arrive rather than dropping the rest
            // of the recording too.
            android.util.Log.w("NovaGattClient", "audio seq gap: expected $expected got $seq")
        }
        expectedAudioSeq = (seq + 1) and 0xFF

        if (block.isNotEmpty()) {
            val pcm = audioDecoder.decodeBlock(block)
            if (pcm.isNotEmpty()) {
                _audioChunks.tryEmit(pcm)
                for (sample in pcm) {
                    utteranceBuffer.write(sample.toInt() and 0xFF)
                    utteranceBuffer.write((sample.toInt() shr 8) and 0xFF)
                }
            }
        }

        if (flags and NovaBleProtocol.AUDIO_FLAG_END != 0) {
            _utteranceActive.value = false
            expectedAudioSeq = null
            if (utteranceBuffer.size() > 0) {
                _completedUtterances.tryEmit(utteranceBuffer.toByteArray())
            }
            utteranceBuffer.reset()
        }
    }
}
