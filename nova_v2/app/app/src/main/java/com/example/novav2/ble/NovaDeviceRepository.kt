package com.example.novav2.ble

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

enum class NovaDeviceConnectionState { DISCONNECTED, CONNECTING, CONNECTED }

sealed class NovaDeviceEvent {
    data object SingleClick : NovaDeviceEvent()
    data object DoubleClick : NovaDeviceEvent()
    data class MultiClick(val count: Int) : NovaDeviceEvent()
    data class Battery(val percent: Int) : NovaDeviceEvent()
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

    private val _lastEvent = MutableStateFlow<NovaDeviceEvent?>(null)
    val lastEvent: StateFlow<NovaDeviceEvent?> = _lastEvent

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
        }
    }

    fun setCommandSender(sender: NovaCommandSender?) {
        _commandSender.value = sender
    }

    fun recordEvent(event: NovaDeviceEvent) {
        _lastEvent.value = event
        if (event is NovaDeviceEvent.Heartbeat) {
            _lastHeartbeatAtMillis.value = System.currentTimeMillis()
        }
    }
}
