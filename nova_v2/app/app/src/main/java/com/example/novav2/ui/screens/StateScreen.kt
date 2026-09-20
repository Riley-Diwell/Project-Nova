package com.example.novav2.ui.screens

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.example.novav2.model.CalendarEventInfo
import com.example.novav2.model.UserState
import com.example.novav2.state.ActivitySignal
import com.example.novav2.state.AmbientCheckOutcome
import com.example.novav2.state.AmbientCheckResult
import com.example.novav2.state.AmbientCheckRunner
import com.example.novav2.state.CalendarSignal
import com.example.novav2.state.CalendarWriter
import com.example.novav2.state.CallStateSignal
import com.example.novav2.state.ForegroundAppSignal
import com.example.novav2.state.LocationSignal
import com.example.novav2.state.PlaceNameLookup
import com.example.novav2.state.SignalRepository
import com.example.novav2.state.UserStateCollector
import com.example.novav2.ui.theme.NovaOk
import com.example.novav2.ui.theme.NovaWarn
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private val timeFormat = SimpleDateFormat("HH:mm", Locale.getDefault())

private fun formatTime(millis: Long): String = timeFormat.format(Date(millis))

/**
 * Live readout of every signal feeding into [UserState] (DESIGN.md §5.2), laid out as a summary
 * strip - confidence, the ambient heartbeat, the next commitment - over grouped detail, so "is
 * everything fine?" answers itself before you read a single row. See Signal Deck (the design
 * pass this came out of) for the two alternatives this didn't become.
 */
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
    var ambientCheckRunning by remember { mutableStateOf(false) }
    val coroutineScope = rememberCoroutineScope()

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

    // Continuous collection (ActivitySignal/SensorSignal updates, and re-snapshotting every 10s)
    // is owned by SignalMonitorService so it keeps running even when this screen isn't open.
    // On entry we just force one immediate refresh so the screen doesn't wait for the next tick.
    LaunchedEffect(Unit) {
        if (LocationSignal.hasPermission(context)) LocationSignal.refresh(context)
        SignalRepository.update(context)
    }

    val liveUserState by SignalRepository.userState.collectAsState()
    LaunchedEffect(liveUserState) {
        liveUserState?.let {
            userState = it
            foregroundAppPermission = ForegroundAppSignal.hasPermission(context)
        }
    }

    // Re-resolved whenever locationCtx actually changes (not every 10s snapshot) -
    // PlaceNameLookup's own cache would absorb the redundant calls anyway, but there's no
    // reason to even ask on every recomposition. Shows "Looking up…" while the async lookup is
    // in flight, distinct from "Unknown" (no location, or the lookup genuinely found nothing).
    var placeName by remember { mutableStateOf("Unknown") }
    LaunchedEffect(userState.locationCtx) {
        val locationCtx = userState.locationCtx
        if (locationCtx == null) {
            placeName = "Unknown"
        } else {
            placeName = "Looking up…"
            placeName = PlaceNameLookup.lookup(context, locationCtx) ?: "Unknown"
        }
    }

    // One shared clock for everything that reads as "how long until/since" (the countdown ring,
    // "last check Xm ago") - ticking once a second locally is plenty; nothing here needs to be
    // pushed from the service itself.
    var nowMillis by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) {
        while (true) {
            nowMillis = System.currentTimeMillis()
            delay(1000L)
        }
    }
    val nextCheckAtMillis by SignalRepository.nextAmbientCheckAtMillis.collectAsState()
    val lastCheck by SignalRepository.lastAmbientCheck.collectAsState()
    val remainingMillis = nextCheckAtMillis?.let {
        (it - nowMillis).coerceIn(0L, SignalRepository.AMBIENT_CHECK_INTERVAL_MILLIS)
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        SummaryStrip(
            confidencePercent = (userState.confidence * 100).toInt(),
            remainingMillis = remainingMillis,
            nextClassLabel = userState.upcomingEvents.firstOrNull()?.displayTime() ?: "None",
        )

        TriggerBanner(
            lastCheck = lastCheck,
            nowMillis = nowMillis,
            running = ambientCheckRunning,
            onTrigger = {
                ambientCheckRunning = true
                coroutineScope.launch {
                    // Deliberately skips the imminent-commitment gate SignalMonitorService's
                    // automatic loop uses - this button's whole point is forcing a real check on
                    // demand, calendar or no calendar.
                    AmbientCheckRunner.run(context, UserStateCollector.snapshot(context))
                    ambientCheckRunning = false
                }
            },
        )

        GroupCard("You right now") {
            DataRow("Activity", userState.activity.toFriendlyWords())
            DataRow("Motion", userState.motion.toFriendlyWords())
            DataRow("Orientation", userState.screenOrientation.toFriendlyWords())
            DataRow(
                "Ambient light",
                userState.ambientLightLux?.let { lightLabel(it) } ?: "Unknown",
            )
            DataRow(
                "Proximity",
                userState.proximityNear?.let { if (it) "Near" else "Far" } ?: "Unknown",
            )
        }

        GroupCard("Focus & calendar") {
            val (focusText, focusTone) = focusLabel(userState.dnd, userState.interruptionFilter)
            ChipRow("Interruptions", focusText, focusTone)
            DataRow("Ringer", userState.ringerMode.toFriendlyWords())
            DataRow(
                "Music",
                userState.musicActive?.let { if (it) "Playing" else "Not playing" } ?: "Unknown",
            )
            val (calendarText, calendarTone) = calendarCtxLabel(userState.calendarCtx)
            ChipRow("Right now", calendarText, calendarTone)

            if (userState.currentEvents.isEmpty() && userState.upcomingEvents.isEmpty()) {
                Spacer(Modifier.height(4.dp))
                Text(
                    "Nothing on the calendar in the next couple of hours.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.outline,
                    modifier = Modifier.padding(vertical = 8.dp),
                )
            } else {
                (userState.currentEvents + userState.upcomingEvents).forEach { event ->
                    Spacer(Modifier.height(6.dp))
                    HorizontalDivider()
                    Spacer(Modifier.height(10.dp))
                    EventDetail(event)
                }
            }
        }

        GroupCard("Device") {
            ChipRow(
                "Battery",
                "${userState.batteryLevelPercent ?: "?"}%" +
                    if (userState.batteryCharging == true) " · charging" else "",
                batteryTone(userState.batteryLevelPercent, userState.batteryCharging),
            )
            DataRow("Foreground app", appLabel(context, userState.foregroundApp))
            ChipRow("Network", userState.networkType.toFriendlyWords(networkFix = true), networkTone(userState.networkType))
            DataRow(
                "Call",
                callStateLabel(userState.callState),
            )
            DataRow(
                "Bluetooth audio",
                userState.bluetoothAudioConnected?.let { if (it) "Connected" else "Not connected" } ?: "Unknown",
            )
            DataRow(
                "Wired headset",
                userState.wiredHeadsetConnected?.let { if (it) "Connected" else "Not connected" } ?: "Unknown",
            )
            DataRow("Location", placeName)
            DataRow(
                "Airplane mode",
                userState.airplaneMode?.let { if (it) "On" else "Off" } ?: "Unknown",
            )
            DataRow(
                "Power save",
                userState.powerSaveMode?.let { if (it) "On" else "Off" } ?: "Unknown",
            )
        }

        DebugToolsSection(
            runtimePermissionsGranted = runtimePermissionsGranted,
            foregroundAppPermission = foregroundAppPermission,
            testEventStatus = testEventStatus,
            onGrantRuntimePermissions = {
                permissionLauncher.launch(
                    arrayOf(
                        Manifest.permission.READ_CALENDAR,
                        Manifest.permission.ACTIVITY_RECOGNITION,
                        Manifest.permission.ACCESS_COARSE_LOCATION,
                        Manifest.permission.ACCESS_FINE_LOCATION,
                        Manifest.permission.READ_PHONE_STATE,
                    )
                )
            },
            onGrantUsageAccess = {
                usageAccessLauncher.launch(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
            },
            onAddTestEvent = {
                if (CalendarWriter.hasPermission(context)) {
                    testEventStatus = createTestEvent(context)
                } else {
                    writePermissionLauncher.launch(Manifest.permission.WRITE_CALENDAR)
                }
            },
        )

        Spacer(Modifier.height(8.dp))
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

// ============================================================================================
// Summary strip
// ============================================================================================

@Composable
private fun SummaryStrip(
    confidencePercent: Int,
    remainingMillis: Long?,
    nextClassLabel: String,
) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        StripTile(label = "Confidence", modifier = Modifier.weight(1f)) {
            Text("$confidencePercent%", style = MaterialTheme.typography.titleLarge)
        }
        StripTile(label = "Next check", modifier = Modifier.weight(1f)) {
            Text(formatCountdown(remainingMillis), style = MaterialTheme.typography.titleLarge)
        }
        StripTile(label = "Next class", modifier = Modifier.weight(1f)) {
            Text(
                nextClassLabel,
                style = MaterialTheme.typography.titleSmall,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
private fun StripTile(label: String, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Column(
        modifier = modifier
            .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(14.dp))
            .padding(vertical = 14.dp, horizontal = 8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        content()
        Text(
            label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

private fun formatCountdown(remainingMillis: Long?): String {
    if (remainingMillis == null) return "–"
    val totalSeconds = remainingMillis / 1000
    return "%d:%02d".format(totalSeconds / 60, totalSeconds % 60)
}

// ============================================================================================
// Ambient trigger banner
// ============================================================================================

@Composable
private fun TriggerBanner(
    lastCheck: AmbientCheckOutcome?,
    nowMillis: Long,
    running: Boolean,
    onTrigger: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.primaryContainer, RoundedCornerShape(14.dp))
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                lastCheckHeadline(lastCheck),
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onPrimaryContainer,
            )
            lastCheck?.let {
                Text(
                    relativeTime(nowMillis - it.atMillis),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = 0.7f),
                )
            }
        }
        Spacer(Modifier.width(12.dp))
        Button(enabled = !running, onClick = onTrigger) {
            Text(if (running) "Checking…" else "Check now")
        }
    }
}

private fun lastCheckHeadline(outcome: AmbientCheckOutcome?): String = when (outcome?.result) {
    null -> "No ambient check has run yet this session"
    AmbientCheckResult.SPOKE -> "Last check: Nova had something to say"
    AmbientCheckResult.QUIET -> "Last check: stayed quiet, nothing urgent"
    AmbientCheckResult.BLOCKED -> "Last check: Nova had something to say, but notifications are off"
    AmbientCheckResult.FAILED -> "Last check: couldn't reach the backend"
}

private fun relativeTime(deltaMillis: Long): String {
    val seconds = deltaMillis / 1000
    return when {
        seconds < 60 -> "just now"
        seconds < 3600 -> "${seconds / 60}m ago"
        else -> "${seconds / 3600}h ${(seconds % 3600) / 60}m ago"
    }
}

// ============================================================================================
// Grouped detail cards
// ============================================================================================

@Composable
private fun GroupCard(title: String, content: @Composable () -> Unit) {
    Column {
        Text(
            title.uppercase(),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.outline,
            modifier = Modifier.padding(start = 4.dp, bottom = 6.dp),
        )
        Card(
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
            shape = RoundedCornerShape(14.dp),
        ) {
            Column(modifier = Modifier.padding(horizontal = 16.dp)) {
                content()
            }
        }
    }
}

@Composable
private fun DataRow(label: String, value: String, caption: String? = null) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 10.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(label, style = MaterialTheme.typography.bodyMedium)
            caption?.let {
                Text(it, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.outline)
            }
        }
        Spacer(Modifier.width(12.dp))
        Text(
            value,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
    HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.2f))
}

@Composable
private fun ChipRow(label: String, chipText: String, tone: Tone) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 10.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        Chip(chipText, tone)
    }
    HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.2f))
}

// ============================================================================================
// Semantic status chip
// ============================================================================================

private enum class Tone { NEUTRAL, OK, WARN, CRITICAL }

@Composable
private fun Chip(text: String, tone: Tone) {
    val color = when (tone) {
        Tone.NEUTRAL -> MaterialTheme.colorScheme.onSurfaceVariant
        Tone.OK -> NovaOk
        Tone.WARN -> NovaWarn
        Tone.CRITICAL -> MaterialTheme.colorScheme.error
    }
    Box(
        modifier = Modifier
            .background(color.copy(alpha = 0.16f), RoundedCornerShape(100))
            .padding(horizontal = 10.dp, vertical = 4.dp),
    ) {
        Text(text, style = MaterialTheme.typography.labelMedium, color = color, fontWeight = FontWeight.SemiBold)
    }
}

// ============================================================================================
// Calendar event detail (unchanged content, restyled to sit inside a GroupCard)
// ============================================================================================

@Composable
private fun EventDetail(event: CalendarEventInfo) {
    val timeStr = if (event.isAllDay) "all day"
    else "${formatTime(event.startMillis)} – ${formatTime(event.endMillis)}"

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(event.title, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold)
        EventDetailRow("Time", timeStr)
        EventDetailRow("Availability", event.availability.toFriendlyWords())
        EventDetailRow("Your RSVP", event.selfStatus.toFriendlyWords())
        event.location?.let { EventDetailRow("Location", it) }
        if (event.minutesUntilStart < 0)
            EventDetailRow("Started", "${-event.minutesUntilStart}min ago")
        else
            EventDetailRow("Starts", event.displayTime())
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

// ============================================================================================
// Debug tools (permissions, test event)
// ============================================================================================

@Composable
private fun DebugToolsSection(
    runtimePermissionsGranted: Boolean,
    foregroundAppPermission: Boolean,
    testEventStatus: String?,
    onGrantRuntimePermissions: () -> Unit,
    onGrantUsageAccess: () -> Unit,
    onAddTestEvent: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(
            "DEBUG TOOLS",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.outline,
            modifier = Modifier.padding(start = 4.dp),
        )
        if (!runtimePermissionsGranted) {
            Button(onClick = onGrantRuntimePermissions, modifier = Modifier.fillMaxWidth()) {
                Text("Grant calendar, activity, location & phone permissions")
            }
        }
        if (!foregroundAppPermission) {
            Button(onClick = onGrantUsageAccess, modifier = Modifier.fillMaxWidth()) {
                Text("Grant usage access (foreground app)")
            }
        }
        Button(onClick = onAddTestEvent, modifier = Modifier.fillMaxWidth()) {
            Text("Add test calendar event")
        }
        testEventStatus?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.outline)
        }
    }
}

// ============================================================================================
// Raw-value -> friendly-label mapping
//
// The debug screen's whole job is showing ground truth, but a raw Android SDK token (a
// package name, a snake_case activity constant, a bare boolean) is ground truth nobody can
// read at a glance. Everything below is presentation only - none of it changes what's sent
// to the backend (UserState's wire shape is untouched).
// ============================================================================================

/** "in_vehicle" -> "In vehicle", "face_up" -> "Face up". Good enough for anything whose raw
 * form is already close to English once the underscores go - see the small dictionaries below
 * for the handful of values that need more than that (offhook, busy_soon, wifi). */
private fun String?.toFriendlyWords(networkFix: Boolean = false): String {
    if (this == null || this == "unknown") return "Unknown"
    if (networkFix && this == "wifi") return "Wi-Fi"
    return split("_").joinToString(" ") { it.replaceFirstChar(Char::uppercase) }
}

private fun callStateLabel(raw: String?): String = when (raw) {
    null, "unknown" -> "Unknown"
    "idle" -> "Idle"
    "ringing" -> "Ringing"
    "offhook" -> "On a call"
    else -> raw.toFriendlyWords()
}

private fun networkTone(raw: String?): Tone = when (raw) {
    "wifi", "ethernet" -> Tone.OK
    "none" -> Tone.WARN
    else -> Tone.NEUTRAL
}

/** Folds DND and the finer-grained interruption filter into one chip - they were two rows
 * describing the same underlying "how interruptible is this person" fact. */
private fun focusLabel(dnd: Boolean, filter: String?): Pair<String, Tone> = when {
    dnd -> "Do Not Disturb" to Tone.WARN
    filter == "priority" -> "Priority only" to Tone.WARN
    filter == "alarms" -> "Alarms only" to Tone.WARN
    filter == "none" -> "Total silence" to Tone.CRITICAL
    filter == "all" -> "All notifications" to Tone.NEUTRAL
    else -> "Unknown" to Tone.NEUTRAL
}

private fun calendarCtxLabel(raw: String?): Pair<String, Tone> = when (raw) {
    "in_event" -> "In a meeting" to Tone.WARN
    "busy_soon" -> "Meeting soon" to Tone.WARN
    "free" -> "Free" to Tone.OK
    else -> "Unknown" to Tone.NEUTRAL
}

private fun lightLabel(lux: Float): String {
    val bucket = when {
        lux < 1f -> "dark"
        lux < 50f -> "dim"
        lux < 1000f -> "bright indoor"
        else -> "bright outdoor"
    }
    return "${lux.toInt()} lux · $bucket"
}

private fun batteryTone(percent: Int?, charging: Boolean?): Tone = when {
    charging == true -> Tone.OK
    percent == null -> Tone.NEUTRAL
    percent < 20 -> Tone.CRITICAL
    percent < 40 -> Tone.WARN
    else -> Tone.NEUTRAL
}

/** "com.google.android.calendar" -> "Calendar", via the same PackageManager label the
 * launcher/app-switcher shows. Falls back to the raw package name if it can't be resolved
 * (uninstalled since, or a system-internal id with no visible label). */
private fun appLabel(context: Context, packageName: String?): String {
    if (packageName == null) return "Unknown"
    val pm = context.packageManager
    return try {
        pm.getApplicationLabel(pm.getApplicationInfo(packageName, 0)).toString()
    } catch (e: PackageManager.NameNotFoundException) {
        packageName
    }
}
