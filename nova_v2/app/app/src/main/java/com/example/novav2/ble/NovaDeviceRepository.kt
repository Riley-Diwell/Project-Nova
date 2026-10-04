package com.example.novav2.ble

import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow

enum class NovaDeviceConnectionState { DISCONNECTED, CONNECTING, CONNECTED }

sealed class NovaDeviceEvent {
    /** [count] presses in one sequence. [mode]/[token] are what SET_MODE last set on the device
     * when the sequence began - null from firmware that predates PRESS. */
    data class Press(val count: Int, val mode: Int? = null, val token: Int? = null) : NovaDeviceEvent()
    /** [millivolts] is the smoothed cell voltage - null from firmware that sends percent only. */
    data class Battery(val percent: Int, val millivolts: Int? = null) : NovaDeviceEvent()
    data object Heartbeat : NovaDeviceEvent()
}

/** What [com.example.novav2.service.NovaDeviceService] exposes so callers elsewhere
 * in the app (e.g. [com.example.novav2.state.AmbientNotifier]) can send a command to
 * the paired device without depending on [NovaGattClient] or Bluetooth APIs directly.
 * Set on [NovaDeviceRepository] only while a device is actually connected — see its
 * doc comment for why every send is a silent no-op otherwise. */
interface NovaCommandSender {
    fun sendHapticPulse(durationMs: Int)
    fun sendLedPulse(durationMs: Int)
    /** A buzz pattern played by the firmware itself - see [NovaCommandFrames.playHaptic]. */
    fun sendHapticPattern(stepsMs: List<Int>)
    /** Adds or replaces (same id) an ambient LED layer - see [NovaLedLayer]. */
    fun sendSetLayer(layer: NovaLedLayer)
    fun sendClearLayer(id: Int)
    /** Tells the device which interaction mode it is in - see [NovaCommandFrames.setMode]. */
    fun sendSetMode(mode: Int, token: Int, timeoutSeconds: Int)
}

/**
 * Live state from the paired Nova device — the BLE analogue of
 * [com.example.novav2.state.SignalRepository]. Read by [com.example.novav2.ui.screens.DeviceScreen]
 * for display; written only by [com.example.novav2.service.NovaDeviceService], which owns the
 * actual GATT connection.
 */
object NovaDeviceRepository {
    private val _connectionState = MutableStateFlow(NovaDeviceConnectionState.DISCONNECTED)
    val connectionState: StateFlow<NovaDeviceConnectionState> = _connectionState

    /** Display only (DeviceScreen's status line), heartbeats left out. A StateFlow conflates
     * equal values, so a second identical press never emits here - anything that acts on
     * presses must collect [events] instead. */
    private val _lastEvent = MutableStateFlow<NovaDeviceEvent?>(null)
    val lastEvent: StateFlow<NovaDeviceEvent?> = _lastEvent

    /** Every press, once each, in order - what a button handler collects. Heartbeats and
     * battery reports are left out ([lastHeartbeatAtMillis] and [battery] cover them) so a
     * collector isn't woken every few seconds for nothing. Hot: an event with no collector at that moment is gone,
     * the same best-effort stance as the firmware's own sendEvent. */
    private val _events = MutableSharedFlow<NovaDeviceEvent>(
        extraBufferCapacity = 16,
        onBufferOverflow = BufferOverflow.DROP_OLDEST, // tryEmit never fails from the GATT thread
    )
    val events: SharedFlow<NovaDeviceEvent> = _events

    /** The device's latest battery report (sent every ~15 s); null until the first one arrives
     * on a connection, and again once it drops - an old reading isn't shown as current. */
    private val _battery = MutableStateFlow<NovaDeviceEvent.Battery?>(null)
    val battery: StateFlow<NovaDeviceEvent.Battery?> = _battery

    private val _lastHeartbeatAtMillis = MutableStateFlow<Long?>(null)
    val lastHeartbeatAtMillis: StateFlow<Long?> = _lastHeartbeatAtMillis

    /** Non-null only while a GATT connection is actually up. Callers must treat a
     * null sender as "no device to send to right now" and skip silently — same
     * best-effort stance as every other optional signal/command path in this
     * codebase (e.g. [com.example.novav2.state.AmbientNotifier.notify]'s own
     * permission check), not something to retry or queue. */
    private val _commandSender = MutableStateFlow<NovaCommandSender?>(null)
    val commandSender: StateFlow<NovaCommandSender?> = _commandSender

    fun setConnectionState(state: NovaDeviceConnectionState) {
        _connectionState.value = state
        if (state != NovaDeviceConnectionState.CONNECTED) {
            // Otherwise a fresh reconnect's stale-heartbeat watchdog (see
            // NovaDeviceService.runKeepalive) would compare against a timestamp
            // left over from the previous connection and immediately conclude
            // the brand new link is already stuck, before it's had a chance to
            // receive its own first heartbeat.
            _lastHeartbeatAtMillis.value = null
            _battery.value = null
        }
    }

    fun setCommandSender(sender: NovaCommandSender?) {
        _commandSender.value = sender
    }

    fun recordEvent(event: NovaDeviceEvent) {
        when (event) {
            is NovaDeviceEvent.Heartbeat -> _lastHeartbeatAtMillis.value = System.currentTimeMillis()
            is NovaDeviceEvent.Battery -> _battery.value = event
            is NovaDeviceEvent.Press -> {
                _lastEvent.value = event
                _events.tryEmit(event)
            }
        }
    }
}
