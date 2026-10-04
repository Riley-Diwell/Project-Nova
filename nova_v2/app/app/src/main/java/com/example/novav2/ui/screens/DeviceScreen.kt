package com.example.novav2.ui.screens

import android.Manifest
import android.companion.CompanionDeviceManager
import android.content.Intent
import android.content.IntentSender
import android.os.Build
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.result.IntentSenderRequest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.background
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Battery1Bar
import androidx.compose.material.icons.filled.Battery3Bar
import androidx.compose.material.icons.filled.Battery5Bar
import androidx.compose.material.icons.filled.BatteryAlert
import androidx.compose.material.icons.filled.BatteryFull
import androidx.compose.material.icons.filled.Bluetooth
import androidx.compose.material.icons.filled.BluetoothConnected
import androidx.compose.material.icons.filled.BluetoothDisabled
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.example.novav2.ble.NovaDeviceConnectionState
import com.example.novav2.ble.NovaDeviceEvent
import com.example.novav2.ble.NovaDevicePairing
import com.example.novav2.ble.NovaDeviceRepository
import com.example.novav2.service.NovaDeviceService
import com.example.novav2.state.DeviceCompass
import com.example.novav2.ui.theme.NovaOk

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
fun DeviceScreen(showSettings: Boolean = true) {
    val context = LocalContext.current
    val connectionState by NovaDeviceRepository.connectionState.collectAsState()
    val lastEvent by NovaDeviceRepository.lastEvent.collectAsState()
    val battery by NovaDeviceRepository.battery.collectAsState()
    val commandSender by NovaDeviceRepository.commandSender.collectAsState()
    val compass by DeviceCompass.status.collectAsState()
    // The status is only refreshed by signal ticks - measure once now so it isn't stale or empty.
    LaunchedEffect(Unit) { DeviceCompass.sync(context) }
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

    // Settings only once paired - and never in onboarding, which only wants the pairing card.
    val withSettings = showSettings && paired
    Column(
        modifier = Modifier
            .fillMaxSize()
            // Only when there is more than the card: a scrolling column can't centre it.
            .then(if (withSettings) Modifier.verticalScroll(rememberScrollState()) else Modifier)
            .padding(20.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = if (withSettings) Arrangement.Top else Arrangement.Center
    ) {
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                val connected = paired && connectionState == NovaDeviceConnectionState.CONNECTED
                Box(
                    Modifier
                        .size(64.dp)
                        .clip(CircleShape)
                        .background(
                            if (connected) NovaOk.copy(alpha = 0.16f)
                            else MaterialTheme.colorScheme.surfaceContainerHighest
                        ),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        when {
                            connected -> Icons.Default.BluetoothConnected
                            paired -> Icons.Default.Bluetooth
                            else -> Icons.Default.BluetoothDisabled
                        },
                        contentDescription = null,
                        tint = if (connected) NovaOk else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(32.dp),
                    )
                }
                Spacer(Modifier.height(16.dp))
                Text(
                    text = statusHeadline(paired, connectionState),
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    text = statusSubtext(paired, connectionState, lastEvent),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                )
                battery?.takeIf { connected }?.let {
                    Spacer(Modifier.height(12.dp))
                    BatteryLevel(it)
                }
                Spacer(Modifier.height(24.dp))

                if (!paired) {
                    Button(onClick = ::beginPairing, modifier = Modifier.fillMaxWidth()) {
                        Text("Pair Nova device")
                    }
                } else {
                    if (commandSender != null) {
                        Button(modifier = Modifier.fillMaxWidth(), onClick = {
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
                    OutlinedButton(modifier = Modifier.fillMaxWidth(), onClick = {
                        context.stopService(Intent(context, NovaDeviceService::class.java))
                        NovaDevicePairing.clearPairedDevice(context)
                        NovaDeviceRepository.setConnectionState(NovaDeviceConnectionState.DISCONNECTED)
                        NovaDeviceRepository.setCommandSender(null)
                        paired = false
                    }) {
                        Text("Forget device", color = MaterialTheme.colorScheme.error)
                    }
                }
            }
        }
        compass?.takeIf { paired }?.let {
            Spacer(Modifier.height(16.dp))
            CompassCard(it, onClear = { DeviceCompass.clear(context) })
        }
        if (withSettings) {
            DeviceSettingsSection(connected = paired && connectionState == NovaDeviceConnectionState.CONNECTED)
        }
    }
}

/** Where the device's compass is pointing, with a way to stop it - a shared place otherwise
 * lasts two hours or until arrival. */
@Composable
private fun CompassCard(status: DeviceCompass.Status, onClear: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    text = "Compass: " + (status.label ?: if (status.manual) "shared place" else "your next trip"),
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    text = status.degrees?.let { "Pointing %.0f° %s".format(it, compassPoint(it)) }
                        ?: "Waiting for a location fix",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            OutlinedButton(onClick = onClear) { Text("Clear") }
        }
    }
}

/** The nearest 8-point name for [degrees] ("N", "NE", ...). */
private fun compassPoint(degrees: Double): String =
    listOf("N", "NE", "E", "SE", "S", "SW", "W", "NW")[(((degrees % 360) + 360 + 22.5) / 45).toInt() % 8]

/** The device's own battery, e.g. "Battery 64% · 3.86 V". The voltage stays visible because the
 * firmware's percent is a curve estimate - it is what to check against a multimeter. */
@Composable
private fun BatteryLevel(battery: NovaDeviceEvent.Battery) {
    val low = battery.percent <= LOW_BATTERY_PERCENT
    val tint = if (low) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant
    Row(verticalAlignment = Alignment.CenterVertically) {
        Icon(batteryIcon(battery.percent), contentDescription = null, tint = tint, modifier = Modifier.size(20.dp))
        Spacer(Modifier.width(6.dp))
        Text(
            text = "Battery ${battery.percent}%" +
                (battery.millivolts?.let { " · %.2f V".format(it / 1000.0) } ?: ""),
            style = MaterialTheme.typography.bodyMedium,
            color = tint,
        )
    }
}

private const val LOW_BATTERY_PERCENT = 15

private fun batteryIcon(percent: Int): ImageVector = when {
    percent <= LOW_BATTERY_PERCENT -> Icons.Default.BatteryAlert
    percent < 40 -> Icons.Default.Battery1Bar
    percent < 65 -> Icons.Default.Battery3Bar
    percent < 90 -> Icons.Default.Battery5Bar
    else -> Icons.Default.BatteryFull
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
    state == NovaDeviceConnectionState.CONNECTING -> "Connecting…"
    else -> "Paired, not connected"
}

private fun statusSubtext(
    paired: Boolean,
    state: NovaDeviceConnectionState,
    lastEvent: NovaDeviceEvent?,
): String = when {
    !paired -> "Pair your Nova companion device to receive clicks and audio, and send it haptic nudges."
    state == NovaDeviceConnectionState.CONNECTED -> "Last event: ${describeEvent(lastEvent)}"
    state == NovaDeviceConnectionState.CONNECTING -> "Reaching your Nova device…"
    else -> "Waiting for your Nova device to come back in range."
}

private fun describeEvent(event: NovaDeviceEvent?): String = when (event) {
    null -> "none yet"
    is NovaDeviceEvent.Press -> if (event.count == 1) "single press" else "${event.count}x press"
    is NovaDeviceEvent.Battery -> "battery ${event.percent}%"
    is NovaDeviceEvent.Heartbeat -> "heartbeat"
    is NovaDeviceEvent.ClearHeading -> "compass cleared"
}
