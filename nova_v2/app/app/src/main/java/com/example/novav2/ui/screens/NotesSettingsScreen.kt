package com.example.novav2.ui.screens

import android.content.Intent
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import com.example.novav2.network.NotesApiClient
import com.example.novav2.notes.NotesRepository
import com.example.novav2.notes.audio.NoteAudioStore
import java.io.File
import java.io.IOException
import java.time.LocalDate
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import com.example.novav2.ui.theme.novaSwitchColors

private val RETENTION_DAYS = listOf(1, 7, 30)

/**
 * Settings -> Notes: export everything,
 * delete everything, and the opt-in to keep dictation audio on this phone.
 */
@Composable
fun NotesSettingsScreen() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var keepAudio by remember { mutableStateOf(NoteAudioStore.isEnabled(context)) }
    var days by remember { mutableStateOf(NoteAudioStore.retentionDays(context)) }
    var status by remember { mutableStateOf<String?>(null) }
    var confirmDeleteAll by remember { mutableStateOf(false) }

    fun export(format: String) = scope.launch {
        status = "Preparing export…"
        try {
            val body = NotesApiClient.export(format)
            val file = withContext(Dispatchers.IO) {
                File(context.cacheDir, "exports").apply { mkdirs() }
                    .resolve("nova-notes-${LocalDate.now()}.${if (format == "md") "md" else "json"}")
                    .apply { writeText(body) }
            }
            val uri = FileProvider.getUriForFile(context, "${context.packageName}.notes-export", file)
            context.startActivity(Intent.createChooser(
                Intent(Intent.ACTION_SEND)
                    .setType(if (format == "md") "text/markdown" else "application/json")
                    .putExtra(Intent.EXTRA_STREAM, uri)
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),
                "Export notes",
            ))
            status = null
        } catch (e: IOException) {
            status = "Export failed: ${e.message}"
        }
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        SettingsBlock {
            Text("Export my notes", style = MaterialTheme.typography.titleMedium)
            Detail("Every note, with summaries and transcripts.")
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 12.dp)) {
                OutlinedButton(onClick = { export("md") }) { Text("Markdown") }
                OutlinedButton(onClick = { export("json") }) { Text("JSON") }
            }
        }

        SettingsBlock {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Keep recordings on this phone", style = MaterialTheme.typography.titleMedium)
                    Detail(
                        "Lets you replay a dictation from any line of its " +
                            "transcript. Audio stays on this phone - it is never uploaded or backed " +
                            "up - and is deleted with its note, when you turn this off, or after " +
                            "the time below. Off by default."
                    )
                }
                Spacer(Modifier.width(16.dp))
                Switch(checked = keepAudio, onCheckedChange = {
                    keepAudio = it
                    NoteAudioStore.setEnabled(context, it)
                }, colors = novaSwitchColors())
            }
            if (keepAudio) {
                Text("Keep recordings for", style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(top = 16.dp, bottom = 8.dp))
                SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                    RETENTION_DAYS.forEachIndexed { i, d ->
                        SegmentedButton(
                            selected = days == d,
                            onClick = { days = d; NoteAudioStore.setRetentionDays(context, d) },
                            shape = SegmentedButtonDefaults.itemShape(i, RETENTION_DAYS.size),
                            label = { Text(if (d == 1) "1 day" else "$d days", maxLines = 1) },
                        )
                    }
                }
            }
        }

        SettingsBlock {
            Text("Delete all notes", style = MaterialTheme.typography.titleMedium)
            Detail("Removes every note, anything Nova learned from them, and any recordings kept " +
                "on this phone. This can't be undone.")
            OutlinedButton(
                onClick = { confirmDeleteAll = true },
                modifier = Modifier.padding(top = 12.dp),
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.error.copy(alpha = 0.6f)),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
            ) {
                Text("Delete all notes")
            }
        }

        status?.let {
            Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 4.dp))
        }
    }

    if (confirmDeleteAll) {
        AlertDialog(
            onDismissRequest = { confirmDeleteAll = false },
            title = { Text("Delete all notes?") },
            text = {
                Text(
                    "Every note will be deleted for good, with its recording and the " +
                        "conversations it came from. Anything Nova learned from them is " +
                        "forgotten. Reminders you set from them are kept. This can't be undone."
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmDeleteAll = false
                    scope.launch {
                        // Everything on this phone at once; the server now, or as soon as it
                        // can be reached (NotesRepository.deleteAll never fails for being offline).
                        NotesRepository(context).deleteAll()
                        status = "All notes deleted."
                    }
                }) { Text("Delete", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { confirmDeleteAll = false }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun SettingsBlock(content: @Composable ColumnScope.() -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(Modifier.padding(16.dp), content = content)
    }
}

@Composable
private fun Detail(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 2.dp),
    )
}
