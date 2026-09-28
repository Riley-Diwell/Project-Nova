package com.example.novav2.ui.screens.onboarding

import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
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
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import com.example.novav2.model.OnboardingAnswers
import com.example.novav2.profile.ProfileRepository
import com.example.novav2.ui.components.CampusDaysQuestion
import com.example.novav2.ui.components.ClassTimesQuestion
import com.example.novav2.ui.components.FocusQuestion
import com.example.novav2.ui.components.ProactivityQuestion
import com.example.novav2.ui.components.QuestionTitle
import com.example.novav2.ui.components.ScreenGutter
import com.example.novav2.ui.components.SleepQuestion
import com.example.novav2.ui.components.TimetableQuestion
import com.example.novav2.ui.components.TravelModeQuestion
import com.example.novav2.ui.screens.DeviceScreen
import kotlinx.coroutines.launch
import java.io.IOException

private const val STEP_NAME = 0
private const val STEP_WEEK = 1
private const val STEP_RHYTHM = 2
private const val STEP_BEHAVIOUR = 3
private const val STEP_DEVICE = 4
private const val STEPS = 5

private val TITLES = listOf("About you", "Your week", "Your rhythm", "How Nova behaves", "Your Nova device")

/**
 * Onboarding: the few questions that let Nova fit the user from day
 * one, after sign-up and before the app. Aimed at under two minutes - every question can be
 * skipped, and the button says "Skip" until something on the step is answered.
 *
 * The privacy summary it would open with is the Welcome screen before sign-up (AuthFlow), so it
 * starts at the name. The answers are saved as a draft on every change, so the app being killed,
 * or the final save failing, loses nothing; the step is saved too, so it resumes where it was.
 * Nothing to navigate on success: NovaApp swaps to the app once [ProfileRepository.state] has a
 * finished profile.
 */
@Composable
fun OnboardingFlow() {
    val initial = remember { ProfileRepository.draft() }
    var step by remember { mutableStateOf(initial.step.coerceIn(0, STEPS - 1)) }
    var name by remember { mutableStateOf(initial.displayName) }
    var answers by remember { mutableStateOf(initial.answers) }
    var saving by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val focusManager = LocalFocusManager.current

    fun persist() = ProfileRepository.saveDraft(ProfileRepository.Draft(name, answers, step))
    fun change(next: OnboardingAnswers) { answers = next; persist() }
    fun goTo(next: Int) { focusManager.clearFocus(); error = null; step = next; persist() }

    fun finish() {
        saving = true
        error = null
        scope.launch {
            try {
                ProfileRepository.completeOnboarding(name, answers)
            } catch (e: IOException) {
                error = "Couldn't save - check your connection and try again. Your answers are kept."
            } finally {
                saving = false
            }
        }
    }

    val answeredThisStep = when (step) {
        STEP_NAME -> name.isNotBlank()
        STEP_WEEK -> answers.campusDays.isNotEmpty() || answers.classTimes.isNotEmpty() ||
            answers.timetableInCalendar != null
        STEP_RHYTHM -> answers.sleepStart != null || answers.sleepEnd != null || answers.travelMode != null
        STEP_BEHAVIOUR -> answers.focusInterruptions != null || answers.proactivity != null
        else -> true
    }

    Surface(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .windowInsetsPadding(WindowInsets.safeDrawing)
                .imePadding()
                .pointerInput(Unit) { detectTapGestures(onTap = { focusManager.clearFocus() }) },
        ) {
            Column(Modifier.padding(start = ScreenGutter, end = ScreenGutter, top = 16.dp)) {
                LinearProgressIndicator(
                    progress = { (step + 1) / STEPS.toFloat() },
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(16.dp))
                Text(
                    "Step ${step + 1} of $STEPS",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(TITLES[step], style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(20.dp))
            }

            if (step == STEP_DEVICE) {
                Text(
                    "Optional. You can pair it later from Settings → Device.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = ScreenGutter),
                )
                Box(Modifier.weight(1f).fillMaxWidth()) { DeviceScreen() }
            } else {
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .verticalScroll(rememberScrollState())
                        .padding(horizontal = ScreenGutter),
                    verticalArrangement = Arrangement.spacedBy(28.dp),
                ) {
                    when (step) {
                        STEP_NAME -> Column {
                            QuestionTitle("What should Nova call you?", "Optional - it's how Nova will address you.")
                            OutlinedTextField(
                                value = name,
                                onValueChange = { name = it.take(60); persist() },
                                label = { Text("Name") },
                                singleLine = true,
                                keyboardOptions = KeyboardOptions(
                                    capitalization = KeyboardCapitalization.Words,
                                    imeAction = ImeAction.Done,
                                ),
                                keyboardActions = KeyboardActions(onDone = { focusManager.clearFocus() }),
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                        STEP_WEEK -> {
                            Column { CampusDaysQuestion(answers, ::change) }
                            Column { ClassTimesQuestion(answers, ::change) }
                            Column { TimetableQuestion(answers, ::change) }
                        }
                        STEP_RHYTHM -> {
                            Column { SleepQuestion(answers, ::change) }
                            Column { TravelModeQuestion(answers, ::change) }
                        }
                        STEP_BEHAVIOUR -> {
                            Column { FocusQuestion(answers, ::change) }
                            Column { ProactivityQuestion(answers, ::change) }
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                }
            }

            error?.let {
                Text(
                    it,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(horizontal = ScreenGutter, vertical = 8.dp),
                )
            }

            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = ScreenGutter, vertical = 16.dp),
            ) {
                if (step > 0) {
                    TextButton(onClick = { goTo(step - 1) }, enabled = !saving) { Text("Back") }
                }
                Spacer(Modifier.weight(1f))
                Button(
                    onClick = { if (step < STEPS - 1) goTo(step + 1) else finish() },
                    enabled = !saving,
                ) {
                    when {
                        saving -> CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                        step == STEP_DEVICE -> Text("Finish")
                        answeredThisStep -> Text("Next")
                        else -> Text("Skip")
                    }
                }
            }
        }
    }
}

/** Signed in, nothing cached yet, asking the server whether onboarding is done. */
@Composable
fun ProfileLoading() {
    Surface(modifier = Modifier.fillMaxSize()) {
        Box(contentAlignment = Alignment.Center) { CircularProgressIndicator() }
    }
}

/**
 * First start on this phone with no connection: there's no way to know yet whether onboarding is
 * done. Retry, or go into the app for now - onboarding shows next time if it's still needed.
 */
@Composable
fun ProfileUnavailable(message: String) {
    Surface(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier.fillMaxSize().padding(24.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(message, style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(24.dp))
            Button(onClick = { ProfileRepository.refresh() }) { Text("Try again") }
            TextButton(onClick = { ProfileRepository.skipForThisRun() }) { Text("Continue anyway") }
        }
    }
}
