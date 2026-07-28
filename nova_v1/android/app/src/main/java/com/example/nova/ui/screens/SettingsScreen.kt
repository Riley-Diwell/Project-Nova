package com.example.nova.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.nova.model.UserProfile

@Composable
fun SettingsScreen(
    profile: UserProfile,
    onNameChange: (String) -> Unit,
    onGoalChange: (Int) -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp)
    ) {
        Text(
            text = "Your profile",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold
        )

        OutlinedTextField(
            value = profile.name,
            onValueChange = onNameChange,
            label = { Text("Name") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth()
        )

        Column {
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
        }
    }
}
