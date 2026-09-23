package com.example.novav2.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Card
import androidx.compose.material3.DateRangePicker
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuAnchorType
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SelectableDates
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDateRangePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.example.novav2.network.NovaApiClient
import kotlinx.coroutines.delay
import java.io.IOException
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import java.time.temporal.ChronoUnit

/** Quick date-range presets shown as chips, plus a custom range via the picker dialog. Range
 * boundaries are computed in UTC throughout, matching Material3's DateRangePicker - which
 * operates on UTC calendar dates by spec - so a selected range and a preset compose the same
 * "since"/"until" instants without a timezone conversion to get wrong. */
private enum class DatePreset(val label: String) {
    ALL("All time"),
    TODAY("Today"),
    LAST_7("Last 7 days"),
    CUSTOM("Date Range"),
}

/** Excludes any day after today (UTC, matching the picker's own calendar semantics) - the
 * audit log can't have entries for a day that hasn't happened yet, so it isn't offered. */
@OptIn(ExperimentalMaterial3Api::class)
private object NotInTheFuture : SelectableDates {
    override fun isSelectableDate(utcTimeMillis: Long): Boolean =
        utcTimeMillis <= System.currentTimeMillis()
}

/** Debounce before a keystroke triggers a re-fetch, so the backend isn't hit on every character. */
private const val SEARCH_DEBOUNCE_MILLIS = 400L

/**
 * The Audit tab (Autonomy pillar): every automated/AI action Nova has taken, and why, so
 * the user can review, search and verify it. Fetch-on-load over GET /audit (schemas/audit.py),
 * re-fetched whenever a filter changes - the backend does the filtering (narration.py), not
 * this screen, so search/date/tool filters all compose into one request.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AuditLogScreen() {
    var entries by remember { mutableStateOf<List<NovaApiClient.AuditEntry>>(emptyList()) }
    var status by remember { mutableStateOf("Loading audit log…") }

    var searchText by remember { mutableStateOf("") }
    var debouncedQuery by remember { mutableStateOf("") }

    var preset by remember { mutableStateOf(DatePreset.ALL) }
    var customStartMillis by remember { mutableStateOf<Long?>(null) }
    var customEndMillis by remember { mutableStateOf<Long?>(null) }
    var showRangePicker by remember { mutableStateOf(false) }

    var toolOptions by remember { mutableStateOf<List<NovaApiClient.ToolGain>>(emptyList()) }
    var selectedTool by remember { mutableStateOf<String?>(null) }
    var toolMenuExpanded by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        try {
            toolOptions = NovaApiClient.getToolGains()
        } catch (e: IOException) {
            // The tool filter just stays empty ("All tools" only) - not fatal to the screen.
        }
    }

    LaunchedEffect(searchText) {
        delay(SEARCH_DEBOUNCE_MILLIS)
        debouncedQuery = searchText
    }

    val filtersActive = preset != DatePreset.ALL || selectedTool != null || debouncedQuery.isNotBlank()

    suspend fun reload() {
        status = "Loading audit log…"
        try {
            val (since, until) = presetRange(preset, customStartMillis, customEndMillis)
            entries = NovaApiClient.getAuditLog(
                since = since,
                until = until,
                tool = selectedTool,
                q = debouncedQuery,
            )
            status = when {
                entries.isNotEmpty() -> ""
                filtersActive -> "No actions match these filters."
                else -> "Nova hasn't taken any automated actions yet."
            }
        } catch (e: IOException) {
            status = "Couldn't reach the Nova backend."
        }
    }

    LaunchedEffect(debouncedQuery, preset, customStartMillis, customEndMillis, selectedTool) {
        reload()
    }

    Column(modifier = Modifier.fillMaxSize()) {
        Text(
            text = "Audit log",
            style = MaterialTheme.typography.titleLarge,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(start = 20.dp, top = 20.dp, end = 20.dp)
        )
        Text(
            text = "Every automated and AI action Nova has taken, and why.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 20.dp, top = 4.dp, end = 20.dp, bottom = 12.dp)
        )

        OutlinedTextField(
            value = searchText,
            onValueChange = { searchText = it },
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp),
            placeholder = { Text("Search actions, notes, replies…") },
            leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
            singleLine = true,
        )

        Spacer(Modifier.height(12.dp))

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp),
            horizontalArrangement = Arrangement.SpaceEvenly,
        ) {
            DatePreset.entries.forEach { option ->
                val label = if (option == DatePreset.CUSTOM) {
                    customRangeLabel(customStartMillis, customEndMillis) ?: option.label
                } else {
                    option.label
                }
                FilterChip(
                    selected = preset == option,
                    onClick = {
                        if (option == DatePreset.CUSTOM) showRangePicker = true else preset = option
                    },
                    label = { Text(label) },
                )
            }
        }

        Spacer(Modifier.height(4.dp))

        ExposedDropdownMenuBox(
            expanded = toolMenuExpanded,
            onExpandedChange = { toolMenuExpanded = it },
            modifier = Modifier.padding(horizontal = 20.dp),
        ) {
            OutlinedTextField(
                value = selectedTool?.let { prettyToolName(it) } ?: "All tools",
                onValueChange = {},
                readOnly = true,
                label = { Text("Tool") },
                trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = toolMenuExpanded) },
                modifier = Modifier
                    .menuAnchor(ExposedDropdownMenuAnchorType.PrimaryNotEditable)
                    .fillMaxWidth(),
            )
            ExposedDropdownMenu(
                expanded = toolMenuExpanded,
                onDismissRequest = { toolMenuExpanded = false },
            ) {
                DropdownMenuItem(
                    text = { Text("All tools") },
                    onClick = { selectedTool = null; toolMenuExpanded = false },
                )
                toolOptions.forEach { tool ->
                    DropdownMenuItem(
                        text = { Text(prettyToolName(tool.name)) },
                        onClick = { selectedTool = tool.name; toolMenuExpanded = false },
                    )
                }
            }
        }

        Spacer(Modifier.height(8.dp))

        if (status.isNotBlank()) {
            Text(
                text = status,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(20.dp),
                textAlign = TextAlign.Center
            )
        }

        LazyColumn(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            contentPadding = PaddingValues(vertical = 12.dp)
        ) {
            // Keyed with the list index as well as the entry's own fields: one episode can run
            // the same tool more than once (e.g. a retry), and every action row it produces
            // shares that episode's occurred_at - so episodeId+tool+occurredAt alone isn't
            // guaranteed unique, and LazyColumn crashes (IllegalArgumentException) on a
            // repeated key.
            itemsIndexed(
                entries,
                key = { index, entry -> "$index:${entry.episodeId}:${entry.tool}:${entry.occurredAt}" },
            ) { _, entry ->
                AuditCard(entry)
            }
        }
    }

    if (showRangePicker) {
        val rangeState = rememberDateRangePickerState(
            initialSelectedStartDateMillis = customStartMillis,
            initialSelectedEndDateMillis = customEndMillis,
            // The audit log has no entries yet for a day that hasn't happened - a future
            // range would only ever come back empty, so it isn't offered as a choice.
            selectableDates = NotInTheFuture,
        )
        Dialog(
            onDismissRequest = { showRangePicker = false },
            properties = DialogProperties(usePlatformDefaultWidth = false),
        ) {
            Surface(
                shape = MaterialTheme.shapes.large,
                modifier = Modifier
                    .fillMaxWidth(0.95f)
                    .fillMaxHeight(0.9f)
                    .padding(vertical = 24.dp),
            ) {
                Column(modifier = Modifier.fillMaxSize()) {
                    // weight(1f) bounds the picker to the space left after the button row,
                    // rather than letting it claim its full natural height and push
                    // Cancel/Apply off the bottom of the dialog where they can't be seen.
                    DateRangePicker(
                        state = rangeState,
                        showModeToggle = false,
                        modifier = Modifier.weight(1f),
                    )
                    Row(
                        horizontalArrangement = Arrangement.End,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(16.dp),
                    ) {
                        TextButton(onClick = { showRangePicker = false }) { Text("Cancel") }
                        Spacer(Modifier.width(8.dp))
                        TextButton(onClick = {
                            val start = rangeState.selectedStartDateMillis
                            val end = rangeState.selectedEndDateMillis
                            if (start != null && end != null) {
                                customStartMillis = start
                                customEndMillis = end
                                preset = DatePreset.CUSTOM
                            }
                            showRangePicker = false
                        }) { Text("Apply") }
                    }
                }
            }
        }
    }
}

@Composable
private fun AuditCard(entry: NovaApiClient.AuditEntry) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    text = entry.summary,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = if (entry.ran) "Ran" else "Refused",
                    style = MaterialTheme.typography.labelMedium,
                    color = if (entry.ran) {
                        MaterialTheme.colorScheme.primary
                    } else {
                        MaterialTheme.colorScheme.error
                    }
                )
            }

            Spacer(Modifier.height(4.dp))

            Text(
                text = formatOccurredAt(entry.occurredAt),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            Spacer(Modifier.height(8.dp))

            Text(
                text = triggerLine(entry),
                style = MaterialTheme.typography.bodyMedium
            )

            entry.reason?.let { reason ->
                Text(
                    text = describeReason(reason),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            entry.speech?.takeIf { it.isNotBlank() }?.let { speech ->
                Spacer(Modifier.height(8.dp))
                Text(
                    text = "\"$speech\"",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}

/** "You asked: ..." when this was a direct request and we know what was said; "Nova noticed:
 * ..." when it was proactive and we know the trigger; a plain fallback otherwise. */
private fun triggerLine(entry: NovaApiClient.AuditEntry): String {
    val context = entry.context
    return when {
        entry.trigger == "requested" && !context.isNullOrBlank() -> "You asked: \"$context\""
        entry.trigger == "requested" -> "You asked"
        !context.isNullOrBlank() -> "Nova noticed: $context"
        else -> "Nova acted on its own"
    }
}

/**
 * [occurredAt] is the backend's `created_at` column - Postgres timestamptz, so an ISO 8601
 * string with a numeric offset (e.g. "2026-09-14T10:00:00+00:00") rather than always "Z" -
 * rendered in this device's timezone. Falls back to the raw string if it doesn't parse,
 * rather than hiding the entry.
 */
private fun formatOccurredAt(occurredAt: String?): String {
    if (occurredAt.isNullOrBlank()) return "Unknown time"
    return try {
        OffsetDateTime.parse(occurredAt)
            .atZoneSameInstant(ZoneId.systemDefault())
            .format(DateTimeFormatter.ofPattern("MMM d, HH:mm"))
    } catch (e: DateTimeParseException) {
        occurredAt
    }
}

/**
 * Plain-English gloss for the Controller's Reason enum (control/controller.py). Falls back
 * to the raw value for a reason the app doesn't yet recognise, rather than dropping it -
 * forward-compatible with a backend that adds new reasons later.
 */
private fun describeReason(reason: String): String = when (reason) {
    "commanded" -> "You asked directly"
    "diverged" -> "Nova noticed a change worth acting on"
    "below_threshold" -> "Confidence was too low to act on its own"
    "no_divergence" -> "Nothing had changed enough to act on"
    "zero_gain" -> "This tool's gain is set to never act on its own"
    "open_loop" -> "No gain is set for this tool yet"
    "no_prediction" -> "Nova had no prediction to compare against"
    "refused_earlier" -> "This tool was already refused earlier in the turn"
    "not_a_function_tool" -> "Not a gain-gated tool"
    else -> reason
}

/** "add_calendar_event" -> "Add calendar event", for the tool filter dropdown. */
private fun prettyToolName(name: String): String =
    name.replace('_', ' ').replaceFirstChar { it.uppercase() }

/** "Sep 1 – 14" once a custom range is picked, formatted in UTC to match the millis
 * Material3's DateRangePicker hands back. Null before anything has been picked. */
private fun customRangeLabel(startMillis: Long?, endMillis: Long?): String? {
    if (startMillis == null || endMillis == null) return null
    val fmt = DateTimeFormatter.ofPattern("MMM d").withZone(ZoneOffset.UTC)
    return "${fmt.format(Instant.ofEpochMilli(startMillis))} – ${fmt.format(Instant.ofEpochMilli(endMillis))}"
}

/** [preset]'s since/until as ISO instants (either or both null for an open range). Computed in
 * UTC throughout - see the [DatePreset] doc comment for why that matches the date picker. */
private fun presetRange(
    preset: DatePreset,
    customStartMillis: Long?,
    customEndMillis: Long?,
): Pair<String?, String?> = when (preset) {
    DatePreset.ALL -> null to null
    DatePreset.TODAY -> Instant.now().truncatedTo(ChronoUnit.DAYS).toString() to null
    DatePreset.LAST_7 -> Instant.now().minus(7, ChronoUnit.DAYS).toString() to null
    DatePreset.CUSTOM -> {
        val since = customStartMillis?.let { Instant.ofEpochMilli(it).toString() }
        // +1 day so the end date is inclusive - the picker hands back UTC midnight of the
        // last selected day, and "until" needs to reach the end of that day, not its start.
        val until = customEndMillis?.let { Instant.ofEpochMilli(it).plus(1, ChronoUnit.DAYS).toString() }
        since to until
    }
}
