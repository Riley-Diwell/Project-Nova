package com.example.nova.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.nova.model.UserProfile

@Composable
fun OnboardingScreen(
    profile: UserProfile,
    onNameChange: (String) -> Unit,
    onGoalChange: (Int) -> Unit,
    onFinish: () -> Unit
) {
    Surface(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(24.dp),
            verticalArrangement = Arrangement.Center
        ) {
            Text(
                text = "Welcome to Nova",
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold
            )
            Spacer(Modifier.height(8.dp))
            Text(
                text = "Let's set up your profile before you pair your device.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Spacer(Modifier.height(32.dp))

            OutlinedTextField(
                value = profile.name,
                onValueChange = onNameChange,
                label = { Text("Your name") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )

            Spacer(Modifier.height(24.dp))

            Text(
                text = "Daily screen time goal: ${profile.dailyGoalMinutes} min",
                style = MaterialTheme.typography.bodyLarge
            )
            Slider(
                value = profile.dailyGoalMinutes.toFloat(),
                onValueChange = { onGoalChange(it.toInt()) },
                valueRange = 30f..480f,
                steps = 14
            )

            Spacer(Modifier.height(32.dp))

            Button(
                onClick = onFinish,
                enabled = profile.name.isNotBlank(),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Get started")
            }
        }
    }
}
