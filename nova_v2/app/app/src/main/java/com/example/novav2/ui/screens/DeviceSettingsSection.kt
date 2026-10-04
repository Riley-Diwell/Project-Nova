package com.example.novav2.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import com.example.novav2.state.DeviceButtonPolicy
import com.example.novav2.state.DeviceInteraction
import com.example.novav2.state.DeviceLayers
import com.example.novav2.state.DevicePreferences
import com.example.novav2.state.IdleAction
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

/** Longest custom instruction - it is sent to Nova as a spoken turn, so a sentence or two. */
private const val MAX_INSTRUCTION_CHARS = 200


/**
 * The paired device's settings, below DeviceScreen's pairing card: what idle presses do (with a
 * guide to what presses do the rest of the time) and the colour of each light cue. Everything is
 * saved as it changes - the device reads none of it; the phone decides what a press means and
 * what each layer looks like, so a change applies on the next press or layer with no reconnect.
 */
@Composable
fun DeviceSettingsSection(connected: Boolean) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var idleActions by remember { mutableStateOf(DevicePreferences.idleActions(context)) }
    var editing by remember { mutableStateOf<Int?>(null) }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
        SectionTitle("Button")
        SettingsBlock {
            Text(
                "Hold the button to talk to Nova, or tap then hold to dictate a note. " +
                    "Tap twice then hold to turn the compass off. " +
                    "When nothing else is going on, presses do this:",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            DevicePreferences.IDLE_PRESS_COUNTS.forEach { count ->
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(
                        Modifier
                            .weight(1f)
                            .clickable { editing = count }
                            .padding(vertical = 8.dp),
                    ) {
                        Text(pressLabel(count), style = MaterialTheme.typography.bodyLarge)
                        Text(
                            DeviceButtonPolicy.describe(idleActions.getValue(count)),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.primary,
                        )
                    }
                    TextButton(onClick = {
                        scope.launch(Dispatchers.IO) { DeviceInteraction.tryIdleAction(context, count) }
                    }) { Text("Try it") }
                }
            }
        }

        SectionTitle("What presses do")
        SettingsBlock { ButtonGuide(idleActions) }

        SectionTitle("Lights")
        SettingsBlock {
            Text(
                "The light is red and green only, so each alert also has its own rhythm - " +
                    "the rhythm is what tells them apart.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            // Same-rhythm clash warning: no two cues share a rhythm yet, so there is nothing to
            // warn about - add it with the notification cue (docs/plans/device-settings-plan.md).
            DeviceLayers.Cue.entries.forEachIndexed { i, cue ->
                if (i > 0) LightDivider()
                CueColourRow(cue, connected)
            }
        }
    }

    editing?.let { count ->
        IdleActionDialog(
            count = count,
            current = idleActions.getValue(count),
            onDismiss = { editing = null },
            onSave = { action ->
                DevicePreferences.setIdleAction(context, count, action)
                idleActions = DevicePreferences.idleActions(context)
                editing = null
            },
        )
    }
}

@Composable
private fun ButtonGuide(idleActions: Map<Int, IdleAction>) {
    DeviceButtonPolicy.guide(idleActions).forEachIndexed { index, row ->
        if (index > 0) Spacer(Modifier.height(12.dp))
        Text(row.situation, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
        row.presses.forEachIndexed { i, what ->
            Text(
                "${pressLabel(i + 1)}: $what",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun LightDivider() {
    HorizontalDivider(Modifier.padding(vertical = 12.dp), color = MaterialTheme.colorScheme.outlineVariant)
}

@Composable
private fun CueColourRow(cue: DeviceLayers.Cue, connected: Boolean) {
    val context = LocalContext.current
    val initial = remember(cue) { DeviceLayers.colourOf(cue).let { (r, g) -> DeviceLayers.Mix.of(r, g) } }
    var mix by remember(cue) { mutableFloatStateOf(initial.toFloat()) }
    val (red, green) = DeviceLayers.Mix.toColour(mix.roundToInt())

    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier
                .size(20.dp)
                .clip(CircleShape)
                .background(Color(red, green, 0)),
        )
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(cueLabel(cue), style = MaterialTheme.typography.bodyLarge)
            Text(
                rhythmLabel(cue.rhythm),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text("Green", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Slider(
            value = mix,
            onValueChange = { mix = it },
            onValueChangeFinished = {
                DevicePreferences.setCueMix(context, cue, mix.roundToInt())
                DeviceLayers.resend() // a live alert in this colour changes now, not next time
            },
            valueRange = 0f..100f,
            modifier = Modifier
                .weight(1f)
                .padding(horizontal = 8.dp),
        )
        Text("Red", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    Row {
        TextButton(enabled = connected, onClick = { DeviceLayers.preview(cue) }) { Text("Preview on device") }
        Spacer(Modifier.weight(1f))
        TextButton(onClick = {
            DevicePreferences.setCueMix(context, cue, null)
            mix = DeviceLayers.Mix.of(cue.red, cue.green).toFloat()
            DeviceLayers.resend()
        }) { Text("Default") }
    }
}

@Composable
private fun IdleActionDialog(
    count: Int,
    current: IdleAction,
    onDismiss: () -> Unit,
    onSave: (IdleAction) -> Unit,
) {
    var preset by remember { mutableStateOf((current as? IdleAction.Use)?.preset) }
    var instruction by remember { mutableStateOf((current as? IdleAction.Custom)?.instruction.orEmpty()) }
    val custom = preset == null
    val chosen: IdleAction? = preset?.let(IdleAction::Use)
        ?: instruction.trim().takeIf { it.isNotEmpty() }?.let(IdleAction::Custom)

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(pressLabel(count)) },
        text = {
            Column {
                IdleAction.Preset.entries.forEach { p ->
                    ChoiceRow(p.label, selected = preset == p) { preset = p }
                }
                ChoiceRow("Your own instruction…", selected = custom) { preset = null }
                if (custom) {
                    OutlinedTextField(
                        value = instruction,
                        onValueChange = { instruction = it.take(MAX_INSTRUCTION_CHARS) },
                        label = { Text("Say to Nova…") },
                        supportingText = { Text("Sent to Nova as if you'd said it. ${instruction.length}/$MAX_INSTRUCTION_CHARS") },
                        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 8.dp),
                    )
                }
            }
        },
        confirmButton = {
            TextButton(enabled = chosen != null, onClick = { chosen?.let(onSave) }) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun ChoiceRow(label: String, selected: Boolean, onClick: () -> Unit) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .selectable(selected = selected, onClick = onClick, role = Role.RadioButton),
    ) {
        RadioButton(selected = selected, onClick = null)
        Spacer(Modifier.width(8.dp))
        Text(label, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.padding(vertical = 10.dp))
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(start = 4.dp, top = 16.dp),
    )
}

@Composable
private fun SettingsBlock(content: @Composable ColumnScope.() -> Unit) {
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(16.dp), content = content)
    }
}

private fun pressLabel(count: Int) = if (count == 1) "1 press" else "$count presses"

private fun cueLabel(cue: DeviceLayers.Cue) = when (cue) {
    DeviceLayers.Cue.REMINDER -> "A reminder went off"
    DeviceLayers.Cue.LEAVE_SOON -> "Leave soon"
    DeviceLayers.Cue.LEAVE_NOW -> "Leave now"
    DeviceLayers.Cue.THINKING -> "Nova is thinking"
}

private fun rhythmLabel(rhythm: DeviceLayers.Rhythm) = when (rhythm) {
    DeviceLayers.Rhythm.SLOW -> "Slow blink"
    DeviceLayers.Rhythm.MEDIUM -> "Steady blink"
    DeviceLayers.Rhythm.FAST -> "Fast blink"
    DeviceLayers.Rhythm.BREATHE -> "Breathing"
}
