package com.example.novav2.ui.screens.onboarding

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.example.novav2.state.ActivitySignal
import com.example.novav2.state.ForegroundAppSignal
import com.example.novav2.state.LocationSignal
import com.example.novav2.state.ReminderScheduler

/**
 * One row per area of the phone Nova uses, each saying why, so the system dialogs never arrive
 * cold. Every row is optional - the feature behind a refused one just stays off, and the screens
 * that need it (Reminders, Device, Agent Status) still ask again in context.
 *
 * Runtime permissions go through the normal dialog. Usage access, exact alarms and (on Android
 * 11+) "Allow all the time" location are Settings pages, so [refresh] re-checks everything on
 * resume. A runtime permission refused twice can't be asked for again; its button then opens the
 * app's Settings page instead.
 */
private sealed class Area(val title: String, val why: String) {
    abstract fun granted(context: Context): Boolean

    /** Runtime permissions, asked for with the system dialog. */
    class Runtime(title: String, why: String, val permissions: Array<String>) : Area(title, why) {
        override fun granted(context: Context) = permissions.all {
            ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
        }
    }

    /** Granted from a Settings page, not a dialog. */
    class Special(
        title: String,
        why: String,
        val isGranted: (Context) -> Boolean,
        val settingsIntent: (Context) -> Intent?,
    ) : Area(title, why) {
        override fun granted(context: Context) = isGranted(context)
    }
}

private val BACKGROUND_LOCATION = Area.Runtime(
    "Location all the time",
    "So a place reminder (\"when I get to the shops\") goes off when you arrive, even with Nova closed.",
    arrayOf(Manifest.permission.ACCESS_BACKGROUND_LOCATION),
)

/** The areas that apply on this Android version, in the order they're shown. */
private fun areas(context: Context): List<Area> = buildList {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) add(Area.Runtime(
        "Notifications",
        "For reminders, \"time to leave\" alerts, and telling you when a note is ready.",
        arrayOf(Manifest.permission.POST_NOTIFICATIONS),
    ))
    add(Area.Runtime(
        "Microphone",
        "To hear you when you talk to Nova or dictate a note - only while you're holding the button or mic.",
        arrayOf(Manifest.permission.RECORD_AUDIO),
    ))
    add(Area.Runtime(
        "Calendar",
        "To see your classes and events so Nova can plan when you should leave, and to add or move events when you ask.",
        arrayOf(Manifest.permission.READ_CALENDAR, Manifest.permission.WRITE_CALENDAR),
    ))
    add(Area.Runtime(
        "Location",
        "To work out travel time for leave reminders and point the device's compass at where you're going.",
        arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION),
    ))
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) add(BACKGROUND_LOCATION)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) add(Area.Runtime(
        "Physical activity",
        "To tell whether you're walking, driving or sitting still, so Nova picks a good moment to speak up.",
        arrayOf(Manifest.permission.ACTIVITY_RECOGNITION),
    ))
    add(Area.Runtime(
        "Phone calls",
        "Only to know when you're on a call, so Nova doesn't interrupt. It never sees who you call.",
        arrayOf(Manifest.permission.READ_PHONE_STATE),
    ))
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) add(Area.Runtime(
        "Nearby devices",
        "To find and stay connected to your Nova device over Bluetooth.",
        arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT),
    ))
    add(Area.Special(
        "Usage access",
        "To know which app you're using, so Nova holds off while you're focused. Turn on Nova in the list that opens.",
        ForegroundAppSignal::hasPermission,
    ) { Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS) })
    // Only Android 12 lets the user take exact alarms away; from 13 USE_EXACT_ALARM is granted at
    // install (see the manifest), so this row never shows there.
    if (!ReminderScheduler.canScheduleExact(context)) add(Area.Special(
        "Alarms & reminders",
        "So reminders go off at the exact minute instead of up to several minutes late.",
        ReminderScheduler::canScheduleExact,
        ReminderScheduler::exactAlarmSettingsIntent,
    ))
}

private fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

private fun appSettingsIntent(context: Context) =
    Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${context.packageName}"))

/** Starts whatever a newly allowed permission switches on, so it works before the next app start. */
private fun startGrantedSignals(context: Context) {
    if (ActivitySignal.hasPermission(context)) ActivitySignal.startUpdates(context)
    if (LocationSignal.hasPermission(context)) LocationSignal.refresh(context)
    ReminderScheduler.reconcileAsync(context) // re-registers place reminders' geofences
}

@Composable
fun PermissionsStep() {
    val context = LocalContext.current
    val shown = remember { areas(context) }

    // Bumped on every resume and every dialog result, so the rows re-read their state.
    var refresh by remember { mutableIntStateOf(0) }
    // Areas asked for at least once this run - a refused one that the system won't ask about
    // again sends the user to Settings instead of silently doing nothing.
    var asked by remember { mutableStateOf(emptySet<String>()) }

    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) refresh++
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    val runtimeLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) {
        startGrantedSignals(context)
        refresh++
    }
    val settingsLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) {
        startGrantedSignals(context)
        refresh++
    }

    fun request(area: Area) {
        when (area) {
            is Area.Runtime -> {
                val activity = context.findActivity()
                val blocked = area.title in asked && activity != null &&
                    area.permissions.none { ActivityCompat.shouldShowRequestPermissionRationale(activity, it) }
                asked = asked + area.title
                if (blocked) settingsLauncher.launch(appSettingsIntent(context))
                else runtimeLauncher.launch(area.permissions)
            }
            is Area.Special -> area.settingsIntent(context)?.let(settingsLauncher::launch)
        }
    }

    // Reading `refresh` here is what makes a resume or a dialog result re-check every row.
    val grantedNow = refresh.let { shown.associateWith { it.granted(context) } }
    val foregroundLocation = Manifest.permission.ACCESS_FINE_LOCATION.let {
        ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
    }
    // Everything a single run of dialogs can cover - not background location, which Android only
    // asks for after foreground location, and not the Settings pages.
    val pendingRuntime = shown.filterIsInstance<Area.Runtime>()
        .filter { it !== BACKGROUND_LOCATION && grantedNow[it] == false }

    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(
            "Nova only uses what you allow here, and each one is optional - skip any and that " +
                "part of Nova stays off. You can change these any time in your phone's Settings.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (pendingRuntime.isNotEmpty()) {
            FilledTonalButton(
                onClick = {
                    asked = asked + pendingRuntime.map { it.title }
                    runtimeLauncher.launch(pendingRuntime.flatMap { it.permissions.toList() }.toTypedArray())
                },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Allow all") }
        }
        shown.forEach { area ->
            val needsForeground = area === BACKGROUND_LOCATION && !foregroundLocation
            PermissionRow(
                area = area,
                granted = grantedNow[area] == true,
                note = if (needsForeground) "Allow Location first." else null,
                enabled = !needsForeground,
                onAllow = { request(area) },
            )
        }
    }
}

@Composable
private fun PermissionRow(area: Area, granted: Boolean, note: String?, enabled: Boolean, onAllow: () -> Unit) {
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh)) {
        Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f).padding(start = 4.dp, end = 8.dp)) {
                Text(area.title, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
                Text(area.why, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (note != null && !granted) {
                    Text(note, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
                }
            }
            if (granted) {
                Text("Allowed", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
            } else {
                TextButton(onClick = onAllow, enabled = enabled) { Text("Allow") }
            }
        }
    }
}
