package com.example.novav2.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.novav2.model.OnboardingAnswers

/*
 * The onboarding questions, one composable per question, shared by
 * the onboarding flow and Settings -> Your profile so both ask exactly the same thing. Each takes
 * the answers and hands back a changed copy; none of them saves anything.
 *
 * Every question can be left blank: tapping a selected single-choice option again clears it.
 */

/** Question title and a one-line explainer, the same shape as Settings' rows. */
@Composable
fun QuestionTitle(title: String, hint: String? = null) {
    Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
    if (hint != null) {
        Text(
            hint,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 2.dp),
        )
    }
    Spacer(Modifier.height(12.dp))
}

@Composable
fun CampusDaysQuestion(answers: OnboardingAnswers, onChange: (OnboardingAnswers) -> Unit) {
    QuestionTitle("Which days are you on campus?", "Nova expects you there on these days.")
    MultiChoiceChips(OnboardingAnswers.DAYS, answers.campusDays) { onChange(answers.copy(campusDays = it)) }
}

@Composable
fun ClassTimesQuestion(answers: OnboardingAnswers, onChange: (OnboardingAnswers) -> Unit) {
    QuestionTitle("When are your classes, usually?")
    MultiChoiceChips(OnboardingAnswers.CLASS_TIMES, answers.classTimes) { onChange(answers.copy(classTimes = it)) }
}

@Composable
fun TimetableQuestion(answers: OnboardingAnswers, onChange: (OnboardingAnswers) -> Unit) {
    QuestionTitle(
        "Is your timetable in your phone's calendar?",
        "If it isn't, Nova leans on your calendar less when working out where you'll be.",
    )
    SingleChoiceChips(OnboardingAnswers.TIMETABLE, answers.timetableInCalendar) {
        onChange(answers.copy(timetableInCalendar = it))
    }
}

@Composable
fun TravelModeQuestion(answers: OnboardingAnswers, onChange: (OnboardingAnswers) -> Unit) {
    QuestionTitle("How do you usually get to uni?", "Used when you ask when to leave.")
    SingleChoiceChips(OnboardingAnswers.TRAVEL_MODES, answers.travelMode) { onChange(answers.copy(travelMode = it)) }
}

@Composable
fun SleepQuestion(answers: OnboardingAnswers, onChange: (OnboardingAnswers) -> Unit) {
    QuestionTitle("When do you usually sleep?", "Nova stays quiet then, except for anything urgent.")
    Row(verticalAlignment = Alignment.CenterVertically) {
        TimeButton(
            label = "Bed",
            value = answers.sleepStart,
            fallback = OnboardingAnswers.DEFAULT_SLEEP_START,
            onPicked = { onChange(answers.copy(sleepStart = it)) },
            modifier = Modifier.weight(1f),
        )
        Spacer(Modifier.width(12.dp))
        TimeButton(
            label = "Up",
            value = answers.sleepEnd,
            fallback = OnboardingAnswers.DEFAULT_SLEEP_END,
            onPicked = { onChange(answers.copy(sleepEnd = it)) },
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
fun FocusQuestion(answers: OnboardingAnswers, onChange: (OnboardingAnswers) -> Unit) {
    QuestionTitle("In class or while focusing, Nova should interrupt you for…")
    RadioOptions(OnboardingAnswers.FOCUS, answers.focusInterruptions) { onChange(answers.copy(focusInterruptions = it)) }
}

@Composable
fun ProactivityQuestion(
    answers: OnboardingAnswers,
    onChange: (OnboardingAnswers) -> Unit,
    hint: String? = "Whether Nova does things without being asked. You can fine-tune each tool later in Settings → Gain.",
) {
    QuestionTitle("How much should Nova act on its own?", hint)
    RadioOptions(OnboardingAnswers.PROACTIVITY, answers.proactivity) { onChange(answers.copy(proactivity = it)) }
}

// --- building blocks ---------------------------------------------------------------------

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun MultiChoiceChips(
    options: List<Pair<String, String>>,
    selected: List<String>,
    onChange: (List<String>) -> Unit,
) {
    // Wraps instead of squeezing: seven day chips don't fit one row on a narrow phone.
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        options.forEach { (value, label) ->
            val on = value in selected
            FilterChip(
                selected = on,
                onClick = {
                    val next = if (on) selected - value else selected + value
                    onChange(options.map { it.first }.filter { it in next }) // keep display order
                },
                label = { Text(label, maxLines = 1) },
            )
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SingleChoiceChips(
    options: List<Pair<String, String>>,
    selected: String?,
    onChange: (String?) -> Unit,
) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        options.forEach { (value, label) ->
            FilterChip(
                selected = selected == value,
                onClick = { onChange(if (selected == value) null else value) },
                label = { Text(label, maxLines = 1) },
            )
        }
    }
}

@Composable
private fun RadioOptions(
    options: List<Pair<String, String>>,
    selected: String?,
    onChange: (String?) -> Unit,
) {
    Column(Modifier.selectableGroup()) {
        options.forEach { (value, label) ->
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier
                    .fillMaxWidth()
                    .selectable(
                        selected = selected == value,
                        onClick = { onChange(if (selected == value) null else value) },
                        role = Role.RadioButton,
                    )
                    .padding(vertical = 4.dp),
            ) {
                RadioButton(selected = selected == value, onClick = null)
                Spacer(Modifier.width(12.dp))
                Text(label, style = MaterialTheme.typography.bodyLarge)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TimeButton(
    label: String,
    value: String?,
    fallback: String,
    onPicked: (String?) -> Unit,
    modifier: Modifier = Modifier,
) {
    var picking by remember { mutableStateOf(false) }
    OutlinedButton(onClick = { picking = true }, modifier = modifier) {
        Text("$label  ${value ?: "–"}", maxLines = 1)
    }
    if (picking) {
        val (hour, minute) = (value ?: fallback).split(":").map { it.toInt() }
        val state = rememberTimePickerState(initialHour = hour, initialMinute = minute)
        AlertDialog(
            onDismissRequest = { picking = false },
            title = { Text(if (label == "Bed") "Usually asleep by" else "Usually up by") },
            text = { TimePicker(state = state) },
            confirmButton = {
                TextButton(onClick = {
                    onPicked("%02d:%02d".format(state.hour, state.minute))
                    picking = false
                }) { Text("OK") }
            },
            dismissButton = {
                Row {
                    if (value != null) TextButton(onClick = { onPicked(null); picking = false }) { Text("Clear") }
                    TextButton(onClick = { picking = false }) { Text("Cancel") }
                }
            },
        )
    }
}
