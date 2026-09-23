package com.example.novav2.ui.screens

import android.Manifest
import android.companion.CompanionDeviceManager
import android.content.Intent
import android.content.IntentSender
import android.os.Build
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.result.IntentSenderRequest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.example.novav2.ble.NovaDeviceConnectionState
import com.example.novav2.ble.NovaDeviceEvent
import com.example.novav2.ble.NovaDevicePairing
import com.example.novav2.ble.NovaDeviceRepository
import com.example.novav2.service.NovaDeviceService

/**
 * Real pairing/connection UI, replacing the earlier fake toggle. Pairing goes through
 * CompanionDeviceManager (see [NovaDevicePairing]'s doc comment for why) rather than a
 * custom scan screen - this composable's job is just requesting BLUETOOTH_CONNECT,
 * launching the system's own device picker, and starting [NovaDeviceService] once a
 * device is chosen. Connection state/events themselves are read from
 * [NovaDeviceRepository], written by that service.
 *
 * Tested against real hardware for the pairing step (see the ScanResult note in
 * the pairing launcher's callback below) - the GATT connection/notify/audio path
 * beyond that is still unverified.
 */
@Composable
fun DeviceScreen() {
    val context = LocalContext.current
    val connectionState by NovaDeviceRepository.connectionState.collectAsState()
    val lastEvent by NovaDeviceRepository.lastEvent.collectAsState()
    val commandSender by NovaDeviceRepository.commandSender.collectAsState()
    // NovaDevicePairing.isPaired reads SharedPreferences directly, which Compose
    // can't observe - reading it as a plain val here only picked up a fresh value
    // when something else (e.g. connectionState) happened to force a recomposition,
    // which is why the screen looked stuck until switching tabs remounted it. Track
    // it as real state instead, updated explicitly at the two places below that
    // actually change it.
    var paired by remember { mutableStateOf(NovaDevicePairing.isPaired(context)) }

    val pairingLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartIntentSenderForResult()
    ) { result ->
        @Suppress("DEPRECATION") // EXTRA_DEVICE's parcelable type is determined by which
        // filter class built the AssociationRequest, not by API level: NovaDevicePairing
        // uses BluetoothLeDeviceFilter, so CDM hands back a android.bluetooth.le.ScanResult
        // here, not a bare BluetoothDevice directly - confirmed against real hardware, this
        // was the crash on device selection the doc comment above flagged as a risk.
        val device = result.data
            ?.getParcelableExtra<android.bluetooth.le.ScanResult>(CompanionDeviceManager.EXTRA_DEVICE)
            ?.device
        if (device != null) {
            NovaDevicePairing.savePairedDevice(context, device.address)
            paired = true
            // Context.startForegroundService() doesn't exist before API 26, and
            // minSdk here is 24 - the compat wrapper (used everywhere else this
            // app starts a foreground service) is required, not optional.
            ContextCompat.startForegroundService(context, Intent(context, NovaDeviceService::class.java))
        }
    }

    val connectPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) startPairing(context, pairingLauncher)
    }

    fun beginPairing() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.BLUETOOTH_CONNECT) !=
            android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            connectPermissionLauncher.launch(Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            startPairing(context, pairingLauncher)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(20.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text(
                    text = statusHeadline(paired, connectionState),
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    text = statusSubtext(paired, connectionState, lastEvent),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Spacer(Modifier.height(20.dp))

                if (!paired) {
                    Button(onClick = ::beginPairing) {
                        Text("Pair Nova device")
                    }
                } else {
                    if (commandSender != null) {
                        Button(onClick = {
                            // Haptic + LED together: one tap that's both feelable and
                            // visible, so it's obvious the command actually reached the
                            // device rather than just leaving the phone.
                            commandSender?.sendHapticPulse(durationMs = 200)
                            commandSender?.sendLedPulse(durationMs = 300)
                        }) {
                            Text("Send test signal")
                        }
                        Spacer(Modifier.height(12.dp))
                    }
                    OutlinedButton(onClick = {
                        context.stopService(Intent(context, NovaDeviceService::class.java))
                        NovaDevicePairing.clearPairedDevice(context)
                        NovaDeviceRepository.setConnectionState(NovaDeviceConnectionState.DISCONNECTED)
                        NovaDeviceRepository.setCommandSender(null)
                        paired = false
                    }) {
                        Text("Forget device")
                    }
                }
            }
        }
    }
}

private fun startPairing(
    context: android.content.Context,
    launcher: androidx.activity.compose.ManagedActivityResultLauncher<IntentSenderRequest, androidx.activity.result.ActivityResult>,
) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return // CompanionDeviceManager needs API 26+
    val manager = context.getSystemService(CompanionDeviceManager::class.java) ?: return
    manager.associate(
        NovaDevicePairing.buildAssociationRequest(),
        object : CompanionDeviceManager.Callback() {
            override fun onDeviceFound(chooserLauncher: IntentSender) {
                launcher.launch(IntentSenderRequestCompat(chooserLauncher))
            }

            override fun onFailure(error: CharSequence?) {
                // Best-effort like every other device/network touch in this codebase -
                // the UI simply stays on "not paired" rather than surfacing a dialog.
            }
        },
        null,
    )
}

/** [ActivityResultContracts.StartIntentSenderForResult] takes an IntentSenderRequest,
 * not a bare IntentSender - this is just that wrapper, named for what it's for here. */
@Suppress("FunctionName")
private fun IntentSenderRequestCompat(sender: IntentSender) =
    androidx.activity.result.IntentSenderRequest.Builder(sender).build()

private fun statusHeadline(paired: Boolean, state: NovaDeviceConnectionState): String = when {
    !paired -> "No device paired"
    state == NovaDeviceConnectionState.CONNECTED -> "Nova device connected"
    state == NovaDeviceConnectionState.CONNECTING -> "Connecting..."
    else -> "Paired, not connected"
}

private fun statusSubtext(
    paired: Boolean,
    state: NovaDeviceConnectionState,
    lastEvent: NovaDeviceEvent?,
): String = when {
    !paired -> "Pair your Nova companion device to receive clicks and audio, and send it haptic nudges."
    state == NovaDeviceConnectionState.CONNECTED -> "Last event: ${describeEvent(lastEvent)}"
    state == NovaDeviceConnectionState.CONNECTING -> "Reaching your Nova device..."
    else -> "Waiting for your Nova device to come back in range."
}

private fun describeEvent(event: NovaDeviceEvent?): String = when (event) {
    null -> "none yet"
    is NovaDeviceEvent.SingleClick -> "single click"
    is NovaDeviceEvent.DoubleClick -> "double click"
    is NovaDeviceEvent.MultiClick -> "${event.count}x click"
    is NovaDeviceEvent.Battery -> "battery ${event.percent}%"
    is NovaDeviceEvent.Heartbeat -> "heartbeat"
}
