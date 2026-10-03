package com.example.novav2.ui.screens

import androidx.activity.ComponentActivity
import androidx.compose.foundation.background
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.EditNote
import androidx.compose.material.icons.filled.RecordVoiceOver
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.automirrored.filled.ShortText
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.novav2.network.NotesApiClient
import com.example.novav2.ui.components.EmptyState
import com.example.novav2.ui.components.ScreenGutter
import com.example.novav2.ui.components.ScreenHeader
import com.example.novav2.ui.components.SectionLabel
import com.example.novav2.viewmodel.NotesViewModel
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import com.example.novav2.ui.components.clearFocusOnTap

/** The Activity-scoped NotesViewModel, shared by the list and the detail view so a delete
 * started in the detail view shows its Undo on the list it returns to. */
@Composable
fun notesViewModel(): NotesViewModel =
    viewModel(viewModelStoreOwner = LocalContext.current as ComponentActivity)

/**
 * The Notes tab: everything the user wrote down, dictated or captured,
 * newest first, grouped by day. Search runs the server's hybrid search (meaning plus exact
 * words), so "Q3" and "COMP2100" are found as well as "what did the tutor say".
 */
@Composable
fun NotesScreen(onOpenNote: (String) -> Unit, vm: NotesViewModel = notesViewModel()) {
    val state by vm.list.collectAsState()
    val snackbar = remember { SnackbarHostState() }
    var composing by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) { vm.refresh() }

    LaunchedEffect(state.pendingDelete?.id) {
        if (state.pendingDelete == null) return@LaunchedEffect
        val result = snackbar.showSnackbar(
            message = "Note deleted", actionLabel = "Undo", duration = SnackbarDuration.Short,
        )
        if (result == SnackbarResult.ActionPerformed) vm.undoDelete()
    }

    Scaffold(
        contentWindowInsets = WindowInsets(0),
        snackbarHost = { SnackbarHost(snackbar) },
        floatingActionButton = {
            FloatingActionButton(onClick = { composing = true }) {
                Icon(Icons.Default.Add, contentDescription = "New note")
            }
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            ScreenHeader(
                title = "Notes",
                subtitle = "Say “note …” to Nova to save one.",
            )

            OutlinedTextField(
                value = state.query,
                onValueChange = vm::setQuery,
                modifier = Modifier.fillMaxWidth().padding(horizontal = ScreenGutter).padding(top = 4.dp),
                placeholder = { Text("Search notes…") },
                leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
                trailingIcon = if (state.query.isNotEmpty()) {
                    { IconButton(onClick = { vm.setQuery("") }) { Icon(Icons.Default.Close, "Clear search") } }
                } else null,
                shape = RoundedCornerShape(28.dp),
                singleLine = true,
            )
            Spacer(Modifier.height(8.dp))

            if (state.pending > 0) {
                Text(
                    "${state.pending} note${if (state.pending > 1) "s" else ""} waiting to upload - " +
                        "they'll appear once Nova can reach the server.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.tertiary,
                    modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp),
                )
            }
            state.error?.let {
                Text(it, color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp))
            }

            if (state.rows.isEmpty() && !state.loading) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    if (state.query.isNotBlank()) {
                        EmptyState(Icons.Default.Search, "No notes match", "Try other words.")
                    } else {
                        EmptyState(Icons.Default.EditNote, "No notes yet", "Tap + to write one, or say “note …” to Nova.")
                    }
                }
            } else if (state.rows.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
            } else {
                NotesList(state.rows, searching = state.query.isNotBlank(), onOpenNote = onOpenNote)
            }
        }
    }

    if (composing) {
        TypedNoteDialog(
            onDismiss = { composing = false },
            onSave = { text ->
                composing = false
                vm.addTyped(text)
            },
        )
    }
}

@Composable
private fun NotesList(rows: List<NotesApiClient.NoteRow>, searching: Boolean, onOpenNote: (String) -> Unit) {
    val zone = ZoneId.systemDefault()
    // Search results are ranked, not chronological - grouping them by day would scramble
    // the ranking, so only the plain list gets day headers.
    val groups: List<Pair<String?, List<NotesApiClient.NoteRow>>> =
        if (searching) listOf(null to rows)
        else rows.groupBy { it.createdAt.atZone(zone).toLocalDate() }
            .map { (day, dayRows) -> dayLabel(day) to dayRows }

    LazyColumn(Modifier.fillMaxSize()) {
        groups.forEach { (header, dayRows) ->
            if (header != null) {
                item(key = "h-$header") {
                    SectionLabel(header, Modifier.padding(start = ScreenGutter, top = 12.dp, bottom = 4.dp))
                }
            }
            items(dayRows, key = { it.id }) { row -> NoteRowCard(row, onClick = { onOpenNote(row.id) }) }
        }
        item { Spacer(Modifier.height(88.dp)) } // clear of the FAB
    }
}

@Composable
private fun NoteRowCard(row: NotesApiClient.NoteRow, onClick: () -> Unit) {
    Card(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.Top) {
            Box(
                Modifier.size(36.dp).background(MaterialTheme.colorScheme.primaryContainer, CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                Icon(kindIcon(row.kind, row.source), contentDescription = row.kind,
                    modifier = Modifier.size(20.dp), tint = MaterialTheme.colorScheme.onPrimaryContainer)
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(row.title, style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(
                    metaLine(row),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                val body = row.snippet ?: row.tldr ?: row.preview.takeIf { it != row.title }
                body?.let {
                    Spacer(Modifier.height(4.dp))
                    Text(it, style = MaterialTheme.typography.bodyMedium, maxLines = 3,
                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.85f),
                        overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

@Composable
private fun TypedNoteDialog(onDismiss: () -> Unit, onSave: (String) -> Unit) {
    var text by remember { mutableStateOf("") }
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { focus.requestFocus() }
    AlertDialog(
        onDismissRequest = onDismiss,
        modifier = Modifier.clearFocusOnTap(),
        title = { Text("New note") },
        text = {
            OutlinedTextField(
                value = text,
                onValueChange = { text = it },
                label = { Text("Note") },
                placeholder = { Text("What do you want to remember?") },
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
                modifier = Modifier.fillMaxWidth().focusRequester(focus),
                minLines = 4,
                maxLines = 10,
            )
        },
        confirmButton = { TextButton(onClick = { onSave(text.trim()) }, enabled = text.isNotBlank()) { Text("Save") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

internal fun kindIcon(kind: String, source: String) = when {
    kind == "dictation" -> Icons.Default.RecordVoiceOver
    source == "typed" -> Icons.Default.EditNote
    else -> Icons.AutoMirrored.Filled.ShortText
}

private val TIME = DateTimeFormatter.ofPattern("h:mm a")
private val DAY = DateTimeFormatter.ofPattern("EEE d MMM")

private fun dayLabel(day: LocalDate): String {
    val today = LocalDate.now()
    return when (day) {
        today -> "Today"
        today.minusDays(1) -> "Yesterday"
        else -> day.format(DAY)
    }
}

private fun metaLine(row: NotesApiClient.NoteRow): String = buildList {
    add(row.createdAt.atZone(ZoneId.systemDefault()).format(TIME))
    row.durationS?.takeIf { it >= 60 }?.let { add("${(it / 60).toInt()} min") }
    row.calendarTitle?.let { add(it) }
    add(sourceLabel(row.source))
    when (row.summaryStatus) {
        "failed" -> add("summary failed")
        "stale" -> add("summary out of date")
    }
    if (row.promoted) add("in what Nova knows")
}.joinToString(" · ")

internal fun sourceLabel(source: String) = when (source) {
    "device_voice" -> "from device"
    "phone_voice" -> "from phone"
    "assistant" -> "via Nova"
    else -> "typed"
}
