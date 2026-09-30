package com.example.novav2.ui.screens

import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalFocusManager
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
import kotlinx.coroutines.launch
import java.io.IOException

/**
 * Settings -> Your profile: onboarding's questions,
 * with the saved answers filled in. Saving sends only what changed, and the server rewrites the
 * matching facts on the Knowledge Map.
 *
 * Proactivity is shown but changing it here doesn't reset the per-tool dials - the user may have
 * tuned those since - so the hint points at the Gain screen instead.
 */
@Composable
fun ProfileScreen() {
    val state by ProfileRepository.state.collectAsState()
    val saved = (state as? ProfileRepository.ProfileState.Ready)?.profile
    var name by remember(saved) { mutableStateOf(saved?.displayName.orEmpty()) }
    var answers by remember(saved) { mutableStateOf(saved?.answers ?: OnboardingAnswers()) }
    var saving by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    var failed by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val focusManager = LocalFocusManager.current

    val dirty = name.trim() != saved?.displayName.orEmpty() || answers != (saved?.answers ?: OnboardingAnswers())
    val change: (OnboardingAnswers) -> Unit = { answers = it; message = null }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .imePadding()
            .pointerInput(Unit) { detectTapGestures(onTap = { focusManager.clearFocus() }) }
            .verticalScroll(rememberScrollState())
            .padding(start = ScreenGutter, end = ScreenGutter, top = 8.dp, bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            "What you told Nova when you set up. Changes show up on your Knowledge Map too.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        ProfileCard {
            QuestionTitle("What should Nova call you?")
            OutlinedTextField(
                value = name,
                onValueChange = { name = it.take(60); message = null },
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
        ProfileCard { CampusDaysQuestion(answers, change) }
        ProfileCard { ClassTimesQuestion(answers, change) }
        ProfileCard { TimetableQuestion(answers, change) }
        ProfileCard { SleepQuestion(answers, change) }
        ProfileCard { TravelModeQuestion(answers, change) }
        ProfileCard { FocusQuestion(answers, change) }
        ProfileCard {
            ProactivityQuestion(
                answers, change,
                hint = "Changing this won't reset your per-tool dials - use Settings → Gain for those.",
            )
        }

        message?.let {
            Text(
                it,
                style = MaterialTheme.typography.bodyMedium,
                color = if (failed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
            )
        }
        Button(
            onClick = {
                focusManager.clearFocus()
                saving = true
                message = null
                scope.launch {
                    try {
                        ProfileRepository.update(name, answers)
                        failed = false
                        message = "Saved."
                    } catch (e: IOException) {
                        failed = true
                        message = "Couldn't save - check your connection and try again."
                    } finally {
                        saving = false
                    }
                }
            },
            enabled = dirty && !saving,
            modifier = Modifier.fillMaxWidth(),
        ) {
            if (saving) CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
            else Text("Save")
        }
        Spacer(Modifier.height(8.dp))
    }
}

@Composable
private fun ProfileCard(content: @Composable () -> Unit) {
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(16.dp)) { content() }
    }
}
