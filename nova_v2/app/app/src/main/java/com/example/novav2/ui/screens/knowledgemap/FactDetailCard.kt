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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
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

/** Everything about one belief, with Edit and Forget - what tapping a fact on the map opens. */
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
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh),
    ) {
        Column(Modifier.padding(start = 16.dp, top = 4.dp, end = 4.dp, bottom = 12.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Column(Modifier.weight(1f).padding(top = 12.dp, end = 4.dp)) {
                    Text(node.label, style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(4.dp))
                    Text(
                        if (node.isDerived) {
                            "NOVA worked this out from your history" +
                                (node.support?.let { " - seen $it times" } ?: "")
                        } else {
                            "You told NOVA this"
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = if (node.isDerived) NovaDerived else MaterialTheme.colorScheme.tertiary,
                    )
                }
                IconButton(onClick = onDismiss) {
                    Icon(Icons.Default.Close, contentDescription = "Close")
                }
            }
            Column(Modifier.padding(end = 12.dp)) {
                node.detail?.takeIf { it.isNotBlank() }?.let {
                    Text("“$it”", style = MaterialTheme.typography.bodySmall,
                        fontStyle = FontStyle.Italic,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 6.dp))
                }
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

                Spacer(Modifier.height(10.dp))
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    OutlinedButton(onClick = onEdit) {
                        Icon(Icons.Default.Edit, contentDescription = null, Modifier.size(18.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("Edit")
                    }
                    OutlinedButton(
                        onClick = onForget,
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
                    ) {
                        Icon(Icons.Default.Delete, contentDescription = null, Modifier.size(18.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("Forget")
                    }
                    // A belief promoted from a note links back to it. Forget removes the
                    // belief and keeps the note; deleting the note removes both.
                    node.noteId?.let { noteId ->
                        TextButton(onClick = { onOpenNote(noteId) }) { Text("Open note") }
                    }
                }
            }
        }
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
