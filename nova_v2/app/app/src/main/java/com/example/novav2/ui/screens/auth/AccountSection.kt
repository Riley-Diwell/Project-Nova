package com.example.novav2.ui.screens.auth

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.novav2.auth.SessionState
import com.example.novav2.viewmodel.SessionViewModel

/** Settings' account block: who's signed in, and signing out (here or on every device). */
@Composable
fun AccountSection(viewModel: SessionViewModel = viewModel()) {
    val session by viewModel.session.collectAsState()
    val signedIn = session as? SessionState.SignedIn ?: return
    // null = no dialog; otherwise whether it's "sign out everywhere".
    var confirming by remember { mutableStateOf<Boolean?>(null) }

    Column {
        Row(verticalAlignment = Alignment.CenterVertically) {
            val email = signedIn.email
            Box(
                Modifier.size(40.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primaryContainer),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    email?.firstOrNull()?.uppercase() ?: "?",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                )
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text("Signed in as", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(email ?: "your account", style = MaterialTheme.typography.bodyLarge,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        Spacer(Modifier.height(12.dp))
        if (viewModel.busy) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(12.dp))
                Text("Signing out…", style = MaterialTheme.typography.bodyMedium)
            }
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedButton(onClick = { confirming = false }) {
                    Text("Sign out")
                }
                Spacer(Modifier.width(8.dp))
                TextButton(onClick = { confirming = true }) {
                    Text("Sign out everywhere", maxLines = 1)
                }
            }
        }
        viewModel.error?.let {
            Text(text = it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
        }
    }

    confirming?.let { everywhere ->
        AlertDialog(
            onDismissRequest = { confirming = null },
            title = { Text(if (everywhere) "Sign out everywhere?" else "Sign out?") },
            text = {
                Text(
                    (if (everywhere) "Every phone signed in to this account will need to sign in again. " else "") +
                        "Your reminders, notes and what Nova has learned stay with your account and come " +
                        "back when you sign in. Chat history on this phone is cleared."
                )
            },
            confirmButton = {
                TextButton(onClick = { confirming = null; viewModel.signOut(everywhere) }) {
                    Text("Sign out")
                }
            },
            dismissButton = {
                TextButton(onClick = { confirming = null }) { Text("Cancel") }
            }
        )
    }

    viewModel.blockedSignOut?.let { blocked ->
        val things = if (blocked.unsent == 1) "1 change hasn't" else "${blocked.unsent} changes haven't"
        AlertDialog(
            onDismissRequest = { viewModel.cancelSignOut() },
            title = { Text("Not everything is saved") },
            text = {
                Text(
                    "$things reached your account yet - you may be offline. Signing out now loses " +
                        "them. Stay signed in and they'll upload when you're back online."
                )
            },
            confirmButton = {
                TextButton(onClick = { viewModel.signOut(blocked.everywhere, force = true) }) {
                    Text("Sign out anyway")
                }
            },
            dismissButton = {
                TextButton(onClick = { viewModel.cancelSignOut() }) { Text("Stay signed in") }
            }
        )
    }
}
