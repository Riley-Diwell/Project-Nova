package com.example.novav2.ui.screens

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CalendarToday
import androidx.compose.material.icons.filled.DeleteOutline
import androidx.compose.material.icons.filled.NotificationsNone
import androidx.compose.material.icons.filled.Place
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SelectableDates
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Switch
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.novav2.ble.NovaDeviceConnectionState
import com.example.novav2.ble.NovaDevicePairing
import com.example.novav2.ble.NovaDeviceRepository
import com.example.novav2.data.ReminderEntity
import com.example.novav2.model.PlaceEvent
import com.example.novav2.model.ReminderOrigin
import com.example.novav2.model.ReminderPriority
import com.example.novav2.model.ReminderRecurrence
import com.example.novav2.model.ReminderStatus
import com.example.novav2.state.GeofenceRegistrar
import com.example.novav2.state.ReminderScheduler
import com.example.novav2.state.ReminderTime
import com.example.novav2.viewmodel.RemindersViewModel
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import com.example.novav2.ui.components.EmptyState
import com.example.novav2.ui.components.ScreenHeader
import com.example.novav2.ui.components.SectionLabel
import com.example.novav2.ui.components.Tag
import com.example.novav2.ui.components.TagSpacing
import com.example.novav2.ui.theme.NovaWarn
import com.example.novav2.ui.theme.novaSwitchColors
import com.example.novav2.ui.components.clearFocusOnTap

private const val WEEK_MILLIS = 7L * 24 * 60 * 60_000

/**
 * Every reminder Nova holds, in the sections the user acts on:
 * what just went off, what's coming, what's being held and why, and what's done or
 * removed. Tick to finish, bin to remove (with Undo), tap to edit, + to add without voice,
 * swipe a done one left to clear it off the list.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RemindersScreen(viewModel: RemindersViewModel = viewModel()) {
    val context = LocalContext.current
    val reminders by viewModel.reminders.collectAsState()
    val exactAllowed by viewModel.exactAlarmsAllowed.collectAsState()
    val notificationsAllowed by viewModel.notificationsAllowed.collectAsState()
    val placeStatus by viewModel.placeStatus.collectAsState()
    val deviceState by NovaDeviceRepository.connectionState.collectAsState()
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    var editing by remember { mutableStateOf<ReminderEntity?>(null) }
    var adding by remember { mutableStateOf(false) }
    var showDone by remember { mutableStateOf(false) }

    // "Allow all the time" can only be asked for once "while using the app" is granted, and on
    // Android 11+ the request opens Settings rather than a dialog. Either result comes back
    // through refreshPermissions, which re-registers the geofences.
    val backgroundLocation = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        viewModel.refreshPermissions()
    }
    val foregroundLocation = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
        if (granted[Manifest.permission.ACCESS_FINE_LOCATION] == true && Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            backgroundLocation.launch(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
        } else viewModel.refreshPermissions()
    }

    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) viewModel.refreshPermissions()
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    val now = System.currentTimeMillis()
    val zone = ZoneId.systemDefault()
    val nowLocal = LocalDateTime.now()
    val needsAttention = reminders.filter { it.statusEnum == ReminderStatus.FIRED }
    val comingUp = reminders.filter { it.statusEnum == ReminderStatus.PENDING }
    val held = reminders.filter { it.statusEnum == ReminderStatus.DEFERRED || it.statusEnum == ReminderStatus.SNOOZED }
    val done = reminders.filter {
        it.statusEnum == ReminderStatus.DONE && it.clearedAtMillis == null &&
            (it.completedAtMillis ?: 0) >= now - WEEK_MILLIS
    }
        .sortedByDescending { it.completedAtMillis }
    val removed = reminders.filter { it.statusEnum == ReminderStatus.CANCELLED && it.updatedAtMillis >= now - WEEK_MILLIS }
        .sortedByDescending { it.updatedAtMillis }

    fun clearWithUndo(r: ReminderEntity) {
        viewModel.clearDone(r.id)
        scope.launch {
            val result = snackbar.showSnackbar("Cleared \"${r.text}\"", actionLabel = "Undo")
            if (result == SnackbarResult.ActionPerformed) viewModel.undoClear(r.id)
        }
    }

    fun deleteWithUndo(r: ReminderEntity) {
        viewModel.delete(r.id)
        scope.launch {
            val result = snackbar.showSnackbar("Removed \"${r.text}\"", actionLabel = "Undo")
            if (result == SnackbarResult.ActionPerformed) viewModel.undoDelete(r.id)
        }
    }

    Scaffold(
        contentWindowInsets = WindowInsets(0),
        snackbarHost = { SnackbarHost(snackbar) },
        floatingActionButton = {
            FloatingActionButton(onClick = { adding = true }) {
                Icon(Icons.Default.Add, contentDescription = "Add a reminder")
            }
        },
    ) { padding ->
      Column(Modifier.fillMaxSize().padding(padding)) {
        ScreenHeader(title = "Reminders", subtitle = "Say “remind me to…” or tap + to add one.")
        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (!exactAllowed) item {
                Banner(
                    "Reminders may be a few minutes late",
                    "Nova isn't allowed to set exact alarms.",
                    "Allow",
                ) { ReminderScheduler.exactAlarmSettingsIntent(context)?.let(context::startActivity) }
            }
            if (!notificationsAllowed) item {
                Banner(
                    "Notifications are off for Nova",
                    "Reminders will only buzz the wearable.",
                    "Turn on",
                ) {
                    val intent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                            .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
                    } else {
                        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                            .setData(android.net.Uri.parse("package:${context.packageName}"))
                    }
                    context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                }
            }
            if (placeStatus == GeofenceRegistrar.Status.NEEDS_BACKGROUND_LOCATION) item {
                Banner(
                    "Place reminders need your location",
                    "Set location to “Allow all the time” so they can go off when you arrive.",
                    "Allow",
                ) {
                    val fine = ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
                        PackageManager.PERMISSION_GRANTED
                    if (!fine) {
                        foregroundLocation.launch(arrayOf(
                            Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION,
                        ))
                    } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                        backgroundLocation.launch(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
                    }
                }
            }
            if (placeStatus == GeofenceRegistrar.Status.UNAVAILABLE) item {
                Banner(
                    "Place reminders are paused",
                    "Location is off, so arriving somewhere can't be noticed.",
                    "Turn on",
                ) {
                    context.startActivity(
                        Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    )
                }
            }
            if (NovaDevicePairing.isPaired(context) && deviceState != NovaDeviceConnectionState.CONNECTED) item {
                Banner("Nova device not connected", "Reminders will arrive on the phone only.", null) {}
            }

            if (reminders.none { it.statusEnum.isActive } && done.isEmpty() && removed.isEmpty()) item {
                EmptyState(Icons.Default.NotificationsNone, "Nothing to remind you about",
                    "Reminders you set will show up here.")
            }

            section("Needs attention", needsAttention) { r ->
                ReminderRow(r, nowLocal, zone, now, onTick = { viewModel.complete(r.id) },
                    onDelete = { deleteWithUndo(r) }, onClick = { editing = r })
            }
            section("Coming up", comingUp) { r ->
                ReminderRow(r, nowLocal, zone, now, onTick = { viewModel.complete(r.id) },
                    onDelete = { deleteWithUndo(r) }, onClick = { editing = r })
            }
            section("Held / snoozed", held) { r ->
                ReminderRow(r, nowLocal, zone, now, onTick = { viewModel.complete(r.id) },
                    onDelete = { deleteWithUndo(r) }, onClick = { editing = r })
            }
            if (done.isNotEmpty()) {
                item {
                    Row(
                        Modifier.fillMaxWidth().padding(top = 8.dp)
                            .clip(MaterialTheme.shapes.small)
                            .clickable { showDone = !showDone }
                            .padding(start = 4.dp, top = 6.dp, bottom = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        SectionLabel("Done (${done.size})", Modifier.weight(1f))
                        Icon(
                            if (showDone) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                            contentDescription = if (showDone) "Hide done" else "Show done",
                            tint = MaterialTheme.colorScheme.primary,
                        )
                    }
                }
                if (showDone) items(done, key = { it.id }) { r ->
                    SwipeToClear(onClear = { clearWithUndo(r) }) {
                        ReminderRow(r, nowLocal, zone, now, onTick = null, onDelete = null, onClick = null)
                    }
                }
            }
            section("Recently removed", removed) { r ->
                Row(Modifier.padding(start = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(r.text, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textDecoration = TextDecoration.LineThrough,
                        maxLines = 2, overflow = TextOverflow.Ellipsis)
                    TextButton(onClick = { viewModel.undoDelete(r.id) }) { Text("Restore") }
                }
            }
            item { Spacer(Modifier.height(72.dp)) }
        }
      }
    }

    if (adding || editing != null) {
        ReminderEditorDialog(
            existing = editing,
            onDismiss = { adding = false; editing = null },
            onSave = { text, due, priority, recurrence ->
                val id = editing?.id
                viewModel.save(id, text, due, priority, recurrence) { ok ->
                    if (ok) {
                        adding = false
                        editing = null
                    } else {
                        scope.launch { snackbar.showSnackbar("That time has already passed") }
                    }
                }
            },
        )
    }
}

private fun androidx.compose.foundation.lazy.LazyListScope.section(
    title: String,
    reminders: List<ReminderEntity>,
    row: @Composable (ReminderEntity) -> Unit,
) {
    if (reminders.isEmpty()) return
    item(key = "header-$title") {
        SectionLabel(title, Modifier.padding(start = 4.dp, top = 12.dp))
    }
    items(reminders, key = { it.id }) { row(it) }
}

/** Swipe left to clear a done reminder off the list. Right-to-left only, so a stray swipe the
 * other way does nothing. */
@Composable
private fun SwipeToClear(onClear: () -> Unit, content: @Composable () -> Unit) {
    val state = rememberSwipeToDismissBoxState()
    SwipeToDismissBox(
        state = state,
        enableDismissFromStartToEnd = false,
        onDismiss = { value -> if (value == SwipeToDismissBoxValue.EndToStart) onClear() },
        backgroundContent = {
            Row(
                Modifier.fillMaxSize()
                    .background(MaterialTheme.colorScheme.surfaceVariant, CardDefaults.shape)
                    .padding(horizontal = 20.dp),
                horizontalArrangement = Arrangement.End,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Clear", style = MaterialTheme.typography.labelLarge)
            }
        },
    ) { content() }
}

@Composable
private fun Banner(title: String, detail: String, action: String?, onAction: () -> Unit) {
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)) {
        Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f).padding(start = 4.dp)) {
                Text(title, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
                Text(detail, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (action != null) TextButton(onClick = onAction) { Text(action) }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ReminderRow(
    r: ReminderEntity,
    nowLocal: LocalDateTime,
    zone: ZoneId,
    nowMillis: Long,
    onTick: (() -> Unit)?,
    onDelete: (() -> Unit)?,
    onClick: (() -> Unit)?,
) {
    val finished = r.statusEnum == ReminderStatus.DONE
    Card(Modifier.fillMaxWidth().let { if (onClick != null) it.clickable(onClick = onClick) else it }) {
        Row(Modifier.padding(horizontal = 4.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = finished, onCheckedChange = if (onTick != null) { _ -> onTick() } else null)
            Column(Modifier.weight(1f).padding(vertical = 4.dp)) {
                Text(
                    r.text,
                    style = MaterialTheme.typography.bodyLarge,
                    color = if (finished) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface,
                    textDecoration = if (finished) TextDecoration.LineThrough else null,
                )
                Text(
                    whenLine(r, nowLocal, zone, nowMillis),
                    style = MaterialTheme.typography.bodySmall,
                    color = if (r.statusEnum == ReminderStatus.FIRED) MaterialTheme.colorScheme.tertiary
                    else MaterialTheme.colorScheme.onSurfaceVariant,
                )
                val important = r.priorityEnum == ReminderPriority.IMPORTANT
                val suggested = r.originEnum == ReminderOrigin.INFERRED
                val repeats = r.recurrence?.let { "Repeats ${it.describe()}" }
                    ?: if (r.isPlaceReminder && r.everyTime) "Every time" else null
                if (important || suggested || repeats != null) FlowRow(
                    modifier = Modifier.padding(top = 6.dp),
                    horizontalArrangement = TagSpacing,
                    verticalArrangement = TagSpacing,
                ) {
                    if (important) Tag("Important", color = NovaWarn)
                    if (repeats != null) Tag(repeats, color = MaterialTheme.colorScheme.tertiary)
                    if (suggested) Tag("Suggested by Nova")
                }
            }
            if (onDelete != null) IconButton(onClick = onDelete) {
                Icon(Icons.Default.DeleteOutline, contentDescription = "Remove",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/** "When you get to the shops" / "When you leave work", with any bounds: "· from tomorrow
 * 12:00am", "· or at 5:00pm". */
private fun placeLine(r: ReminderEntity, nowLocal: LocalDateTime = LocalDateTime.now()): String? {
    val place = r.place ?: return null
    val zone = ZoneId.systemDefault()
    val where = when (place.on) {
        PlaceEvent.ARRIVE -> "When you get to ${place.label}"
        PlaceEvent.LEAVE -> "When you leave ${place.label}"
    }
    val after = r.placeAfterLocal?.let { ReminderTime.parseLocal(it, zone) }?.takeIf { it.isAfter(nowLocal) }
    val deadline = ReminderTime.parseLocal(r.dueLocal, zone)
    return where +
        (after?.let { " · from ${ReminderTime.describe(it, nowLocal)}" } ?: "") +
        (deadline?.let { " · or at ${ReminderTime.describe(it, nowLocal)}" } ?: "")
}

/** The secondary line: when it's due (or where), or why it's held and when it will arrive. */
private fun whenLine(r: ReminderEntity, nowLocal: LocalDateTime, zone: ZoneId, nowMillis: Long): String {
    val due = ReminderTime.parseLocal(r.dueLocal, zone)
    val dueText = placeLine(r, nowLocal) ?: due?.let { ReminderTime.describe(it, nowLocal) } ?: r.dueLocal
    return when (r.statusEnum) {
        ReminderStatus.DEFERRED -> {
            val reason = if (r.deferReason == "call") "your call" else r.deferReason ?: "a busy moment"
            "Held during $reason · delivers ${ReminderTime.clock(r.triggerAtMillis, zone)}"
        }
        ReminderStatus.SNOOZED -> "Snoozed · back at ${ReminderTime.clock(r.triggerAtMillis, zone)}"
        ReminderStatus.FIRED -> {
            val ago = r.firedAtMillis?.let { (nowMillis - it) / 60_000L }
            val place = r.place
            when {
                ago == null || ago < 1 -> "Just now"
                place != null -> "Went off ${agoText(ago)} · ${if (place.on == PlaceEvent.ARRIVE) "at" else "leaving"} ${place.label}"
                else -> "Went off ${agoText(ago)} · was due $dueText"
            }
        }
        ReminderStatus.DONE -> r.completedAtMillis?.let {
            "Done ${ReminderTime.describe(ReminderTime.toLocal(it, zone), nowLocal)}"
        } ?: "Done"
        else -> dueText
    }
}

/** Minutes under two hours, then whole hours, then whole days once it's been a day. */
private fun agoText(minutes: Long): String = when {
    minutes < 120 -> "$minutes min ago"
    minutes < 60 * 24 -> "${minutes / 60} hours ago"
    minutes < 60 * 48 -> "1 day ago"
    else -> "${minutes / (60 * 24)} days ago"
}

/** Days before today can't be picked - a reminder is always for later. Compared as the picker's
 * own UTC-midnight millis, the same convention it hands back. */
@OptIn(ExperimentalMaterial3Api::class)
private object TodayOrLater : SelectableDates {
    override fun isSelectableDate(utcTimeMillis: Long): Boolean =
        utcTimeMillis >= LocalDate.now().atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()

    override fun isSelectableYear(year: Int): Boolean = year >= LocalDate.now().year
}

/** Add or edit. The time picks default to the existing reminder's, or the next whole hour. */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun ReminderEditorDialog(
    existing: ReminderEntity?,
    onDismiss: () -> Unit,
    onSave: (String, LocalDateTime?, ReminderPriority, ReminderRecurrence?) -> Unit,
) {
    val zone = ZoneId.systemDefault()
    val original = existing?.let { ReminderTime.parseLocal(it.dueLocal, zone) }
    val initial = original ?: LocalDateTime.now().plusHours(1).withMinute(0).withSecond(0).withNano(0)

    var text by remember { mutableStateOf(existing?.text.orEmpty()) }
    var date by remember { mutableStateOf(initial.toLocalDate()) }
    var time by remember { mutableStateOf(initial.toLocalTime()) }
    var important by remember { mutableStateOf(existing?.priorityEnum == ReminderPriority.IMPORTANT) }
    var frequency by remember { mutableStateOf(existing?.recurFrequency) }
    var pickingDate by remember { mutableStateOf(false) }
    var pickingTime by remember { mutableStateOf(false) }

    val focus = remember { FocusRequester() }
    if (existing == null) LaunchedEffect(Unit) { focus.requestFocus() }

    val chosen = LocalDateTime.of(date, time)
    // A place reminder keeps its place - it's set by voice ("when I get to the shops"), and here
    // only its text and importance change.
    val atPlace = existing?.let { placeLine(it) }
    // An edit that leaves the time alone keeps it, even if it's already passed (a reminder that
    // went off and is only having its text fixed). Anything else has to be in the future.
    val timeUnchanged = existing != null && (chosen == original || atPlace != null)
    val inPast = !timeUnchanged && !chosen.isAfter(LocalDateTime.now())

    AlertDialog(
        onDismissRequest = onDismiss,
        modifier = Modifier.clearFocusOnTap(),
        title = { Text(if (existing == null) "New reminder" else "Edit reminder") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    value = text, onValueChange = { text = it },
                    label = { Text("Remind me to…") },
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
                    modifier = Modifier.fillMaxWidth().focusRequester(focus),
                )
                if (atPlace != null) Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Default.Place, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.width(8.dp))
                    Text(atPlace, style = MaterialTheme.typography.bodyLarge)
                } else Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    PickerField(Icons.Default.CalendarToday, "Date", date.format(DateTimeFormatter.ofPattern("EEE d MMM yyyy")),
                        error = inPast) { pickingDate = true }
                    PickerField(Icons.Default.Schedule, "Time", ReminderTime.clock(chosen), error = inPast) { pickingTime = true }
                    if (inPast) {
                        Text(
                            "That time has already passed - pick a later one.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.error,
                            modifier = Modifier.padding(start = 4.dp),
                        )
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Important", style = MaterialTheme.typography.bodyLarge)
                        Text("Breaks through when you're busy", style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Spacer(Modifier.width(12.dp))
                    Switch(checked = important, onCheckedChange = { important = it }, colors = novaSwitchColors())
                }
                if (atPlace == null) Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("Repeat", style = MaterialTheme.typography.bodyLarge)
                    // Wraps onto a second line instead of squeezing - a fixed Row of five chips
                    // in a dialog's width crushed "Monthly" into a vertical column of letters.
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(0.dp),
                    ) {
                        (listOf<String?>(null) + ReminderRecurrence.FREQUENCIES).forEach { f ->
                            FilterChip(
                                selected = frequency == f,
                                onClick = { frequency = f },
                                label = { Text(f?.replaceFirstChar { it.uppercase() } ?: "Never", maxLines = 1) },
                            )
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                enabled = text.isNotBlank() && !inPast,
                onClick = {
                    // An edit that leaves the time alone passes null, so a reminder that already
                    // went off isn't re-armed just because its text changed.
                    val due = if (timeUnchanged) null else chosen
                    val recurrence = frequency?.let {
                        if (it == existing?.recurFrequency) existing.recurrence else ReminderRecurrence(it)
                    }
                    onSave(text.trim(), due, if (important) ReminderPriority.IMPORTANT else ReminderPriority.NORMAL, recurrence)
                },
            ) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )

    if (pickingDate) {
        val state = rememberDatePickerState(
            initialSelectedDateMillis = date.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli(),
            selectableDates = TodayOrLater,
        )
        DatePickerDialog(
            onDismissRequest = { pickingDate = false },
            confirmButton = {
                TextButton(onClick = {
                    // The picker's millis are midnight UTC of the chosen day, whatever the zone.
                    state.selectedDateMillis?.let {
                        date = Instant.ofEpochMilli(it).atZone(ZoneOffset.UTC).toLocalDate()
                    }
                    pickingDate = false
                }) { Text("OK") }
            },
            dismissButton = { TextButton(onClick = { pickingDate = false }) { Text("Cancel") } },
        ) { DatePicker(state = state) }
    }

    if (pickingTime) {
        val state = rememberTimePickerState(initialHour = time.hour, initialMinute = time.minute)
        AlertDialog(
            onDismissRequest = { pickingTime = false },
            title = { Text("Select time") },
            confirmButton = {
                TextButton(onClick = {
                    time = LocalTime.of(state.hour, state.minute)
                    pickingTime = false
                }) { Text("OK") }
            },
            dismissButton = { TextButton(onClick = { pickingTime = false }) { Text("Cancel") } },
            text = {
                Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                    TimePicker(state = state)
                }
            },
        )
    }
}

/** A tappable "Date  Mon 28 Sep" row that opens a picker - full width, so the value never has
 * to share a line with another button. */
@Composable
private fun PickerField(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    label: String,
    value: String,
    error: Boolean,
    onClick: () -> Unit,
) {
    val borderColour = if (error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.outline
    Row(
        Modifier
            .fillMaxWidth()
            .clip(MaterialTheme.shapes.extraSmall)
            .border(1.dp, borderColour, MaterialTheme.shapes.extraSmall)
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, contentDescription = null, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(12.dp))
        Text(label, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f))
        Text(value, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium,
            color = if (error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
            maxLines = 1)
    }
}
