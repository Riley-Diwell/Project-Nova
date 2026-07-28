package com.example.nova.ui.screens

import android.Manifest
import android.content.Context
import android.content.Intent
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AirplanemodeActive
import androidx.compose.material.icons.filled.Apps
import androidx.compose.material.icons.filled.BatteryFull
import androidx.compose.material.icons.filled.BatterySaver
import androidx.compose.material.icons.filled.BluetoothAudio
import androidx.compose.material.icons.filled.CalendarToday
import androidx.compose.material.icons.filled.DirectionsRun
import androidx.compose.material.icons.filled.DirectionsWalk
import androidx.compose.material.icons.filled.DoNotDisturb
import androidx.compose.material.icons.filled.DoNotDisturbOn
import androidx.compose.material.icons.filled.Event
import androidx.compose.material.icons.filled.Headset
import androidx.compose.material.icons.filled.LightMode
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.Phone
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.ScreenRotation
import androidx.compose.material.icons.filled.Sensors
import androidx.compose.material.icons.filled.Smartphone
import androidx.compose.material.icons.filled.Stairs
import androidx.compose.material.icons.filled.VolumeOff
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.nova.model.CalendarEventInfo
import com.example.nova.model.UserState
import com.example.nova.state.ActivitySignal
import com.example.nova.state.CalendarSignal
import com.example.nova.state.CalendarWriter
import com.example.nova.state.CallStateSignal
import com.example.nova.state.ForegroundAppSignal
import com.example.nova.state.LocationSignal
import com.example.nova.state.SensorSignal
import com.example.nova.state.UserStateCollector
import kotlinx.coroutines.delay
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private val REFRESH_INTERVAL_MILLIS = 5_000L
private val timeFormat = SimpleDateFormat("HH:mm", Locale.getDefault())

private fun formatTime(millis: Long): String = timeFormat.format(Date(millis))

/** Live readout of every signal (DESIGN.md §5.2) feeding into [UserState]. */
@Composable
fun StateScreen() {
    val context = LocalContext.current

    var userState by remember { mutableStateOf(UserStateCollector.snapshot(context)) }

    var runtimePermissionsGranted by remember {
        mutableStateOf(
            CalendarSignal.hasPermission(context) &&
                ActivitySignal.hasPermission(context) &&
                LocationSignal.hasPermission(context) &&
                CallStateSignal.hasPermission(context)
        )
    }
    var foregroundAppPermission by remember {
        mutableStateOf(ForegroundAppSignal.hasPermission(context))
    }
    var testEventStatus by remember { mutableStateOf<String?>(null) }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        runtimePermissionsGranted = results.values.all { it }
        if (ActivitySignal.hasPermission(context)) ActivitySignal.startUpdates(context)
        if (LocationSignal.hasPermission(context)) LocationSignal.refresh(context)
        userState = UserStateCollector.snapshot(context)
    }

    val usageAccessLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) {
        foregroundAppPermission = ForegroundAppSignal.hasPermission(context)
        userState = UserStateCollector.snapshot(context)
    }

    val writePermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        testEventStatus = if (granted) createTestEvent(context) else "Calendar write permission denied."
    }

    DisposableEffect(Unit) {
        if (ActivitySignal.hasPermission(context)) ActivitySignal.startUpdates(context)
        SensorSignal.startUpdates(context)
        if (LocationSignal.hasPermission(context)) LocationSignal.refresh(context)
        onDispose {
            ActivitySignal.stopUpdates(context)
            SensorSignal.stopUpdates(context)
        }
    }

    LaunchedEffect(Unit) {
        while (true) {
            delay(REFRESH_INTERVAL_MILLIS)
            if (LocationSignal.hasPermission(context)) LocationSignal.refresh(context)
            foregroundAppPermission = ForegroundAppSignal.hasPermission(context)
            userState = UserStateCollector.snapshot(context)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(24.dp)) {
                Text(
                    "Confidence: ${(userState.confidence * 100).toInt()}%",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary
                )
                Spacer(Modifier.height(16.dp))

                // ── Device & context signals ──────────────────────────────────────
                val signals = listOf(
                    Triple(Icons.Default.DirectionsWalk, "Activity", userState.activity ?: "unknown"),
                    Triple(Icons.Default.CalendarToday, "Calendar", userState.calendarCtx ?: "unknown"),
                    Triple(Icons.Default.DoNotDisturbOn, "DND", if (userState.dnd) "on" else "off"),
                    Triple(Icons.Default.DoNotDisturb, "Interruption filter", userState.interruptionFilter ?: "unknown"),
                    Triple(Icons.Default.VolumeOff, "Ringer mode", userState.ringerMode ?: "unknown"),
                    Triple(Icons.Default.Smartphone, "Screen", if (userState.screen) "on" else "off"),
                    Triple(Icons.Default.ScreenRotation, "Orientation", userState.screenOrientation ?: "unknown"),
                    Triple(Icons.Default.DirectionsRun, "Motion", userState.motion ?: "unknown"),
                    Triple(Icons.Default.Stairs, "Steps (since reboot)", userState.stepCountSinceBoot?.toString() ?: "unknown"),
                    Triple(Icons.Default.LightMode, "Ambient light", userState.ambientLightLux?.let { "%.0f lux".format(it) } ?: "unknown"),
                    Triple(Icons.Default.Sensors, "Proximity", userState.proximityNear?.let { if (it) "near" else "far" } ?: "unknown"),
                    Triple(Icons.Default.MusicNote, "Music active", userState.musicActive?.let { if (it) "yes" else "no" } ?: "unknown"),
                    Triple(Icons.Default.BatterySaver, "Power save mode", userState.powerSaveMode?.let { if (it) "on" else "off" } ?: "unknown"),
                    Triple(Icons.Default.AirplanemodeActive, "Airplane mode", userState.airplaneMode?.let { if (it) "on" else "off" } ?: "unknown"),
                    Triple(Icons.Default.Phone, "Call state", userState.callState ?: "unknown"),
                    Triple(Icons.Default.LocationOn, "Location", userState.locationCtx ?: "unknown"),
                    Triple(Icons.Default.Wifi, "Network", userState.networkType ?: "unknown"),
                    Triple(
                        Icons.Default.BatteryFull,
                        "Battery",
                        userState.batteryLevelPercent?.let {
                            "$it%" + if (userState.batteryCharging == true) " (charging)" else ""
                        } ?: "unknown"
                    ),
                    Triple(Icons.Default.Headset, "Wired headset", userState.wiredHeadsetConnected?.let { if (it) "connected" else "not connected" } ?: "unknown"),
                    Triple(Icons.Default.BluetoothAudio, "Bluetooth audio", userState.bluetoothAudioConnected?.let { if (it) "connected" else "not connected" } ?: "unknown"),
                    Triple(Icons.Default.Apps, "Foreground app", userState.foregroundApp ?: "unknown"),
                )

                signals.forEach { (icon, label, value) ->
                    SignalRow(icon, label, value)
                    HorizontalDivider(modifier = Modifier.padding(vertical = 14.dp))
                }

                // ── Current calendar events ───────────────────────────────────────
                SectionHeader(
                    icon = Icons.Default.Event,
                    title = if (userState.currentEvents.isEmpty())
                        "Current events - none"
                    else
                        "Current events (${userState.currentEvents.size})"
                )
                if (userState.currentEvents.isEmpty()) {
                    HorizontalDivider(modifier = Modifier.padding(vertical = 14.dp))
                } else {
                    userState.currentEvents.forEachIndexed { i, event ->
                        Spacer(Modifier.height(12.dp))
                        EventDetail(event)
                        HorizontalDivider(modifier = Modifier.padding(vertical = 14.dp))
                    }
                }

                // ── Upcoming calendar events ──────────────────────────────────────
                SectionHeader(
                    icon = Icons.Default.Schedule,
                    title = if (userState.upcomingEvents.isEmpty())
                        "Upcoming events - none"
                    else
                        "Upcoming events (${userState.upcomingEvents.size})"
                )
                if (userState.upcomingEvents.isEmpty()) {
                    HorizontalDivider(modifier = Modifier.padding(vertical = 14.dp))
                } else {
                    userState.upcomingEvents.forEachIndexed { i, event ->
                        Spacer(Modifier.height(12.dp))
                        EventDetail(event)
                        HorizontalDivider(modifier = Modifier.padding(vertical = 14.dp))
                    }
                }

                // ── Permission buttons & test action ─────────────────────────────
                if (!runtimePermissionsGranted) {
                    Spacer(Modifier.height(8.dp))
                    Button(onClick = {
                        permissionLauncher.launch(
                            arrayOf(
                                Manifest.permission.READ_CALENDAR,
                                Manifest.permission.ACTIVITY_RECOGNITION,
                                Manifest.permission.ACCESS_COARSE_LOCATION,
                                Manifest.permission.READ_PHONE_STATE,
                            )
                        )
                    }) {
                        Text("Grant calendar, activity, location & phone permissions")
                    }
                }

                if (!foregroundAppPermission) {
                    Spacer(Modifier.height(12.dp))
                    Button(onClick = {
                        usageAccessLauncher.launch(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
                    }) {
                        Text("Grant usage access (foreground app)")
                    }
                }

                Spacer(Modifier.height(8.dp))
                Button(onClick = {
                    if (CalendarWriter.hasPermission(context)) {
                        testEventStatus = createTestEvent(context)
                    } else {
                        writePermissionLauncher.launch(Manifest.permission.WRITE_CALENDAR)
                    }
                }) {
                    Text("Add test calendar event")
                }
                testEventStatus?.let {
                    Spacer(Modifier.height(12.dp))
                    Text(it, style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
    }
}

/** DESIGN.md §5.2 spike check: confirms CalendarWriter's insert path actually lands an event. */
private fun createTestEvent(context: Context): String {
    val start = System.currentTimeMillis() + 60 * 60 * 1000L
    val end = start + 60 * 60 * 1000L
    val uri = CalendarWriter.createEvent(
        context = context,
        title = "Nova test event",
        startMillis = start,
        endMillis = end,
        description = "Created by Nova's CalendarWriter spike."
    )
    return if (uri != null) "Created: $uri" else "Failed - no writable calendar found."
}

@Composable
private fun SectionHeader(icon: ImageVector, title: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.width(12.dp))
        Text(title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
    }
}

/** Full detail block for a single [CalendarEventInfo]. */
@Composable
private fun EventDetail(event: CalendarEventInfo) {
    val timeStr = if (event.isAllDay) "all day"
    else "${formatTime(event.startMillis)} – ${formatTime(event.endMillis)}"

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(start = 28.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)
    ) {
        Text(event.title, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold)
        EventDetailRow("Time", timeStr)
        EventDetailRow("Availability", event.availability)
        EventDetailRow("Your RSVP", event.selfStatus)
        event.location?.let { EventDetailRow("Location", it) }
        EventDetailRow("Priority score", "${event.priorityScore()}/6")
        if (event.minutesUntilStart < 0)
            EventDetailRow("Started", "${-event.minutesUntilStart}min ago")
        else
            EventDetailRow("Starts in", event.displayTime())
    }
}

@Composable
private fun EventDetailRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.outline)
        Text(value, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun SignalRow(icon: ImageVector, label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(12.dp))
            Text(label, style = MaterialTheme.typography.bodyLarge)
        }
        Text(
            text = value,
            style = MaterialTheme.typography.bodyLarge,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}
