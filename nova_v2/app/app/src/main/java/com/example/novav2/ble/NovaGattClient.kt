package com.example.novav2.ble

import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.content.Context
import android.os.Handler
import android.os.Looper
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.receiveAsFlow

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

    // One ordered stream of recordings: Start, Pcm..., End (see AudioFrame). A Channel rather
    // than a SharedFlow because nothing may be dropped - a 60-minute capture is ~115k frames,
    // and a SharedFlow's tryEmit silently discards once its buffer is full. One consumer
    // (NovaDeviceService), unbounded: the transcriber normally runs well ahead of real time,
    // and a transient backlog costs memory rather than a gap in the transcript.
    private val _audioFrames = Channel<AudioFrame>(Channel.UNLIMITED)
    val audioFrames: Flow<AudioFrame> = _audioFrames.receiveAsFlow()

    private val assemblerLock = Any()
    private val assembler = AudioFrameAssembler(
        emit = { _audioFrames.trySend(it) },
        onSequenceGap = { expected, got ->
            // A notification was dropped somewhere between firmware and here - this is
            // exactly what the sequence number exists to catch (see docs/ble-protocol.md).
            // Nothing to recover mid-utterance; keep decoding what does arrive.
            android.util.Log.w("NovaGattClient", "audio seq gap: expected $expected got $got")
        },
    )

    // Drives the assembler's inactivity finaliser: a recording with no frame for 1.5 s is
    // closed (marked truncated), so a lost END frame never leaves one open forever.
    private val mainHandler = Handler(Looper.getMainLooper())
    private val inactivityCheck = object : Runnable {
        override fun run() {
            val stillOpen = synchronized(assemblerLock) {
                assembler.checkInactivity()
                assembler.active
            }
            if (stillOpen) mainHandler.postDelayed(this, INACTIVITY_POLL_MS)
        }
    }

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
        endOpenRecording()
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
                    // A capture cut off by the link dropping is still saved - whatever
                    // arrived becomes the recording, marked truncated.
                    endOpenRecording()
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
        val opened = synchronized(assemblerLock) {
            val wasActive = assembler.active
            assembler.onFrame(frame)
            !wasActive && assembler.active
        }
        if (opened) {
            mainHandler.removeCallbacks(inactivityCheck)
            mainHandler.postDelayed(inactivityCheck, INACTIVITY_POLL_MS)
        }
    }

    private fun endOpenRecording() {
        synchronized(assemblerLock) { assembler.forceEnd() }
        mainHandler.removeCallbacks(inactivityCheck)
    }

    private companion object {
        // How often the inactivity finaliser looks; the timeout itself is the assembler's.
        const val INACTIVITY_POLL_MS = 500L
    }
}
