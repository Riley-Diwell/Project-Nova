package com.example.novav2.ui.screens

import android.content.Intent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bookmark
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.example.novav2.network.NotesApiClient
import com.example.novav2.ui.components.NoteText
import com.example.novav2.ui.components.SectionLabel
import com.example.novav2.notes.audio.NoteAudioPlayer
import com.example.novav2.viewmodel.NotesViewModel
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.launch
import com.example.novav2.ui.components.clearFocusOnTap

/** Where "Add to what Nova knows" can file a note - top-level branches of the ontology the
 * Knowledge Map already shows (persona/models.py). */
private val PROMOTE_CATEGORIES = listOf(
    "About me" to listOf("facts"),
    "Something I like or dislike" to listOf("opinions"),
    "A routine" to listOf("routines"),
    "Health" to listOf("facts", "health"),
)

/**
 * One note: editable title and text, the summary card for a long note,
 * the transcript in timestamped segments (tap one to replay it, if the audio was kept on this
 * phone), and the actions - re-summarise, share, add to what Nova knows, delete.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun NoteDetailScreen(noteId: String, onClosed: () -> Unit, vm: NotesViewModel = notesViewModel()) {
    val state by vm.detail.collectAsState()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val player = remember { NoteAudioPlayer(scope) }
    var playingFrom by remember { mutableStateOf<Double?>(null) }
    var editing by remember(noteId) { mutableStateOf(false) }
    var promoting by remember { mutableStateOf(false) }
    var confirmingDelete by remember { mutableStateOf(false) }

    LaunchedEffect(noteId) { vm.openNote(noteId) }
    DisposableEffect(Unit) { onDispose { player.stop() } }

    val note = state.note
    if (note == null) {
        Box(Modifier.fillMaxSize().padding(20.dp), contentAlignment = Alignment.Center) {
            if (state.loading) CircularProgressIndicator()
            state.error?.let { Text(it, color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center) }
        }
        return
    }

    fun play(from: Double) {
        if (playingFrom == from) {
            player.stop(); playingFrom = null
        } else {
            playingFrom = from
            player.play(context, note.id, from) { if (playingFrom == from) playingFrom = null }
        }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp)) {
        Text(note.displayTitle, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(4.dp))
        Text(detailMeta(note), style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (state.busy) {
            Spacer(Modifier.height(8.dp))
            CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
        }
        state.error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }

        Spacer(Modifier.height(16.dp))
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            OutlinedButton(onClick = { editing = true }) { Text("Edit") }
            if (note.kind != "quick" || note.summaryStatus in setOf("failed", "stale")) {
                OutlinedButton(onClick = vm::resummarise, enabled = !state.busy) {
                    Text(if (note.summary == null) "Summarise" else "Re-summarise")
                }
            }
            OutlinedButton(onClick = {
                scope.launch {
                    vm.markdown(note.id)?.let { md ->
                        context.startActivity(Intent.createChooser(
                            Intent(Intent.ACTION_SEND).setType("text/markdown").putExtra(Intent.EXTRA_TEXT, md)
                                .putExtra(Intent.EXTRA_SUBJECT, note.displayTitle),
                            "Share note",
                        ))
                    }
                }
            }) { Text("Share") }
            if (note.promotedFactIds.isEmpty()) {
                OutlinedButton(onClick = { promoting = true }, enabled = !state.busy) { Text("Add to what Nova knows") }
            }
            OutlinedButton(
                onClick = { confirmingDelete = true },
                colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
            ) { Text("Delete") }
        }
        if (note.promotedFactIds.isNotEmpty()) {
            Text("Part of what Nova knows about you - see the Map tab.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.tertiary,
                modifier = Modifier.padding(top = 6.dp))
        }

        note.summary?.let { SummaryCard(it, note.summaryStatus, onPlay = if (state.audioKept) ::play else null) }
        when (note.summaryStatus) {
            "failed" -> Text("The summary couldn't be made - try Re-summarise.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(top = 8.dp))
            "stale" -> Text("Edited since it was summarised.", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 8.dp))
        }

        Spacer(Modifier.height(16.dp))
        // A spoken note Nova has read back: the words as heard stay the record, with Nova's
        // reading underneath to take or leave.
        val meant = note.interpretedText?.takeIf { it.isNotBlank() && it.trim() != note.text.trim() }
        if (note.segments.isNotEmpty()) {
            SectionLabel("Transcript")
            if (note.sttEngine != null) {
                Text("Auto-transcribed, may contain errors." + if (state.audioKept) " Tap a line to hear it." else "",
                    style = MaterialTheme.typography.bodySmall, fontStyle = FontStyle.Italic,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.height(6.dp))
            note.segments.forEach { seg ->
                Row(Modifier.fillMaxWidth().clickable(enabled = state.audioKept) { play(seg.startS) }.padding(vertical = 3.dp)) {
                    Text(clock(seg.startS), style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.primary, modifier = Modifier.width(48.dp))
                    Text(seg.text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                    if (playingFrom == seg.startS) Icon(Icons.Default.Stop, "Stop", Modifier.size(16.dp))
                    else if (state.audioKept) Icon(Icons.Default.PlayArrow, "Play", Modifier.size(16.dp),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        } else {
            if (meant != null) {
                SectionLabel("What Nova heard")
                Spacer(Modifier.height(6.dp))
            }
            NoteText(note.text)
        }
        if (meant != null) {
            InterpretationCard(meant, enabled = !state.busy, onUse = { vm.saveEdits(null, meant) })
        }
        Spacer(Modifier.height(24.dp))
    }

    if (editing) {
        NoteEditorDialog(note, onDismiss = { editing = false }, onSave = { title, text ->
            editing = false
            vm.saveEdits(title.takeIf { it != note.displayTitle }, text.takeIf { it != note.text })
        })
    }

    if (promoting) {
        PromoteDialog(onDismiss = { promoting = false }, onPick = { category ->
            promoting = false
            vm.promote(category)
        })
    }

    // A hard delete: confirmed here instead of undone afterwards, because afterwards there is
    // nothing left to restore (docs/plans/notes-hard-delete-plan.md phase 3).
    if (confirmingDelete) {
        AlertDialog(
            onDismissRequest = { confirmingDelete = false },
            title = { Text("Delete this note?") },
            text = {
                Text(
                    "This note, its recording, and the conversation it came from will be deleted " +
                        "for good. Anything Nova learned from it is forgotten. Reminders you set " +
                        "from it are kept. This can't be undone."
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmingDelete = false
                    vm.delete(note.id)
                    onClosed()
                }) { Text("Delete", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { confirmingDelete = false }) { Text("Cancel") } },
        )
    }
}

/** Nova's reading of a spoken note, under what was heard. "Use this version" replaces the
 * note's text with it - which, like any edit, the server then treats as the user's own words. */
@Composable
private fun InterpretationCard(meant: String, enabled: Boolean, onUse: () -> Unit) {
    Card(
        Modifier.fillMaxWidth().padding(top = 16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Column(Modifier.padding(16.dp)) {
            Text("What Nova thinks you said", style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold)
            Text("Likely mis-hearings fixed and punctuation added. What was heard is kept above.",
                style = MaterialTheme.typography.bodySmall, fontStyle = FontStyle.Italic,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(10.dp))
            NoteText(meant)
            Spacer(Modifier.height(4.dp))
            TextButton(onClick = onUse, enabled = enabled) { Text("Use this version") }
        }
    }
}

@Composable
private fun NoteEditorDialog(note: NotesApiClient.Note, onDismiss: () -> Unit, onSave: (String, String) -> Unit) {
    var title by remember { mutableStateOf(note.displayTitle) }
    var text by remember { mutableStateOf(note.text) }
    AlertDialog(
        onDismissRequest = onDismiss,
        modifier = Modifier.clearFocusOnTap(),
        title = { Text("Edit note") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    title, { title = it },
                    label = { Text("Title") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    text, { text = it },
                    label = { Text("Note") },
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
                    modifier = Modifier.fillMaxWidth(),
                    minLines = 5,
                    maxLines = 12,
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { onSave(title.trim(), text.trim()) }, enabled = text.isNotBlank()) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun SummaryCard(summary: NotesApiClient.Summary, status: String, onPlay: ((Double) -> Unit)?) {
    Card(
        Modifier.fillMaxWidth().padding(top = 16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh),
    ) {
        Column(Modifier.padding(16.dp)) {
            SectionLabel("Summary")
            Spacer(Modifier.height(6.dp))
            Text(summary.tldr, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium)
            Bulleted("Key points", summary.keyPoints)
            Bulleted("Action items", summary.actionItems)
            Bulleted("Open questions", summary.openQuestions)
            if (summary.flaggedMoments.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                Text("Flagged moments", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
                summary.flaggedMoments.forEach { m ->
                    Row(Modifier.clickable(enabled = onPlay != null) { onPlay?.invoke(m.tS) }.padding(vertical = 2.dp)) {
                        Icon(Icons.Default.Bookmark, null, Modifier.size(14.dp).padding(top = 2.dp),
                            tint = MaterialTheme.colorScheme.tertiary)
                        Spacer(Modifier.width(6.dp))
                        Text("${clock(m.tS)}  “${m.quote}”", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
            if (status == "stale") {
                Text("Summary is from before your last edit.", style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 6.dp))
            }
        }
    }
}

@Composable
private fun Bulleted(heading: String, items: List<String>) {
    if (items.isEmpty()) return
    Spacer(Modifier.height(10.dp))
    Text(heading, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
    items.forEach {
        Row(Modifier.padding(top = 3.dp)) {
            Text("•", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.width(14.dp))
            Text(it, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        }
    }
}

@Composable
private fun PromoteDialog(onDismiss: () -> Unit, onPick: (List<String>) -> Unit) {
    var choice by remember { mutableStateOf(0) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Add to what Nova knows") },
        text = {
            Column {
                Text("Nova will treat this note as something true about you, and use it in " +
                    "future answers. You can remove it from the Map tab at any time.",
                    style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.height(8.dp))
                PROMOTE_CATEGORIES.forEachIndexed { i, (label, _) ->
                    Row(verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.fillMaxWidth().clickable { choice = i }) {
                        RadioButton(selected = choice == i, onClick = { choice = i })
                        Text(label, style = MaterialTheme.typography.bodyLarge)
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { onPick(PROMOTE_CATEGORIES[choice].second) }) { Text("Add") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

private val DETAIL_TIME = DateTimeFormatter.ofPattern("EEE d MMM, h:mm a")

private fun detailMeta(note: NotesApiClient.Note): String = buildList {
    add(note.createdAt.atZone(ZoneId.systemDefault()).format(DETAIL_TIME))
    note.calendarTitle?.let { add(it) }
    note.durationS?.takeIf { it >= 60 }?.let { add("${(it / 60).toInt()} min") }
    add(sourceLabel(note.source))
}.joinToString(" · ")

internal fun clock(seconds: Double): String {
    val total = seconds.toInt()
    val h = total / 3600
    val m = (total % 3600) / 60
    val s = total % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%d:%02d".format(m, s)
}
