package com.example.novav2.ui.screens.auth

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

/**
 * The first screen for someone with no account on this phone: a plain-language summary of what
 * Nova collects and where it goes, before they sign up.
 * Keep it in step with docs/privacy-and-security.md once that exists.
 */
@Composable
fun WelcomeScreen(onContinue: () -> Unit) {
    Surface(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
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
                text = "An assistant that speaks up when it matters, and stays quiet when you're busy.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Spacer(Modifier.height(28.dp))

            PrivacyPoint(
                "What Nova uses",
                "What you say to it, your calendar, and signals like your location, activity and " +
                    "screen time, so it can tell when to help."
            )
            PrivacyPoint(
                "Where it goes",
                "Nova's own server, which runs the AI model and the database itself - " +
                    "nothing goes to an AI company. Travel times come from Google Maps, and " +
                    "web searches go out through the server's private search."
            )
            PrivacyPoint(
                "Saved to your account",
                "Your reminders and notes, so they come back when you sign in on another phone."
            )
            PrivacyPoint(
                "What stays on your phone",
                "Your chat history. Your sign-in is kept encrypted and isn't included in backups."
            )
            PrivacyPoint(
                "You're in control",
                "You can see what Nova has learned, correct or delete it, and sign out at any time."
            )

            Spacer(Modifier.height(24.dp))
            Button(onClick = onContinue, modifier = Modifier.fillMaxWidth()) {
                Text("Continue")
            }
        }
    }
}

@Composable
private fun PrivacyPoint(title: String, body: String) {
    Column(modifier = Modifier.padding(bottom = 16.dp)) {
        Text(text = title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
        Text(
            text = body,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}
