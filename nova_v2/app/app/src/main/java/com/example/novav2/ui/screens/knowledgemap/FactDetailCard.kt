package com.example.novav2.ui.screens.knowledgemap

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.History
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import com.example.novav2.network.NovaApiClient
import com.example.novav2.ui.theme.NovaDerived
import com.example.novav2.ui.components.clearFocusOnTap
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException

/** One belief - its wording, where it's filed, and Edit / History / Forget - what tapping a fact on the map opens. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun FactDetail(
    node: NovaApiClient.GraphNode,
    groupTitle: String?,
    onDismiss: () -> Unit,
    onEdit: () -> Unit,
    onForget: () -> Unit,
    onOpenNote: (String) -> Unit = {},
) {
    var showHistory by remember(node.id) { mutableStateOf(false) }

    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh),
    ) {
        Column(Modifier.padding(start = 16.dp, top = 4.dp, end = 4.dp, bottom = 12.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Column(Modifier.weight(1f).padding(top = 12.dp, end = 4.dp)) {
                    Text(node.label, style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold)
                    val filed = listOfNotNull(
                        groupTitle,
                        node.category.takeIf { it.isNotEmpty() }
                            ?.joinToString(" › ") { it.replaceFirstChar(Char::uppercase) },
                    ).distinct()
                    if (filed.isNotEmpty()) {
                        Text(filed.joinToString("  ·  "),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(top = 4.dp))
                    }
                }
                IconButton(onClick = onDismiss) {
                    Icon(Icons.Default.Close, contentDescription = "Close")
                }
            }
            Spacer(Modifier.height(10.dp))
            FlowRow(
                Modifier.padding(end = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                OutlinedButton(onClick = onEdit) {
                    Icon(Icons.Default.Edit, contentDescription = null, Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Edit")
                }
                OutlinedButton(onClick = { showHistory = true }) {
                    Icon(Icons.Default.History, contentDescription = null, Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("History")
                }
                OutlinedButton(
                    onClick = onForget,
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
                ) {
                    Icon(Icons.Default.Delete, contentDescription = null, Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Forget")
                }
            }
        }
    }

    if (showHistory) {
        FactHistoryDialog(
            node = node,
            onDismiss = { showHistory = false },
            onOpenNote = { showHistory = false; onOpenNote(it) },
        )
    }
}

/**
 * How a belief came to read the way it does, oldest first: how NOVA first learned it, then
 * each rewording the user made from the map (the server's `edits`, logged by PATCH /persona).
 */
@Composable
private fun FactHistoryDialog(
    node: NovaApiClient.GraphNode,
    onDismiss: () -> Unit,
    onOpenNote: (String) -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("History") },
        text = {
            Column(
                Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                HistoryEntry(node.createdAt) {
                    if (node.isDerived) {
                        Text("NOVA worked this out from your history" +
                            (node.support?.let { " - seen $it times" } ?: ""),
                            style = MaterialTheme.typography.bodyMedium, color = NovaDerived)
                    } else {
                        // The user's own words when the server kept them; otherwise the
                        // belief as it read before its first edit.
                        val original = node.detail?.takeIf { it.isNotBlank() }
                            ?: node.edits.firstOrNull()?.from
                            ?: node.label
                        Text("You originally told NOVA:", style = MaterialTheme.typography.bodyMedium)
                        Quote(original)
                    }
                    // A belief promoted from a note links back to it. Forget removes the
                    // belief and keeps the note; deleting the note removes both.
                    node.noteId?.let { noteId ->
                        TextButton(onClick = { onOpenNote(noteId) }) { Text("Open note") }
                    }
                }
                node.edits.forEach { edit ->
                    HistoryEntry(edit.at) {
                        Text("You updated this node from", style = MaterialTheme.typography.bodyMedium)
                        Quote(edit.from)
                        Text("to", style = MaterialTheme.typography.bodyMedium)
                        Quote(edit.to)
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )
}

@Composable
private fun HistoryEntry(at: String?, content: @Composable () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(formatHistoryTime(at), style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        content()
    }
}

@Composable
private fun Quote(text: String) {
    Text("“$text”", style = MaterialTheme.typography.bodyMedium,
        fontStyle = FontStyle.Italic,
        color = MaterialTheme.colorScheme.tertiary)
}

private val HISTORY_TIME = DateTimeFormatter.ofPattern("EEE d MMM yyyy, h:mm a")

/** A server timestamp (ISO 8601 with an offset) in this device's timezone; the raw string if it won't parse. */
private fun formatHistoryTime(iso: String?): String {
    if (iso.isNullOrBlank()) return "Date unknown"
    return try {
        OffsetDateTime.parse(iso).atZoneSameInstant(ZoneId.systemDefault()).format(HISTORY_TIME)
    } catch (e: DateTimeParseException) {
        iso
    }
}

@Composable
internal fun EditFactDialog(
    node: NovaApiClient.GraphNode,
    onDismiss: () -> Unit,
    onSave: (String) -> Unit,
) {
    var draft by remember(node.id) { mutableStateOf(node.label) }
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { focus.requestFocus() }

    AlertDialog(
        onDismissRequest = onDismiss,
        modifier = Modifier.clearFocusOnTap(),
        title = { Text("Edit what NOVA believes") },
        text = {
            OutlinedTextField(
                value = draft,
                onValueChange = { draft = it },
                label = { Text("Belief") },
                minLines = 2,
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
                modifier = Modifier.fillMaxWidth().focusRequester(focus),
            )
        },
        confirmButton = {
            TextButton(
                onClick = { onSave(draft.trim()) },
                enabled = draft.isNotBlank() && draft.trim() != node.label,
            ) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
internal fun ForgetFactDialog(
    node: NovaApiClient.GraphNode,
    onDismiss: () -> Unit,
    onForget: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Forget this?") },
        text = { Text("NOVA will stop believing “${node.label}” and won't use it again.") },
        confirmButton = {
            TextButton(onClick = onForget) { Text("Forget", color = MaterialTheme.colorScheme.error) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
