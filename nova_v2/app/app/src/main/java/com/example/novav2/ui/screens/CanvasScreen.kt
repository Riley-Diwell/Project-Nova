package com.example.novav2.ui.screens

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import com.example.novav2.network.CanvasApiClient
import java.io.IOException
import kotlinx.coroutines.launch

/** Nova is built for ANU students, so the address starts filled in - still editable for anyone
 * whose Canvas lives elsewhere. */
private const val DEFAULT_CANVAS_ADDRESS = "canvas.anu.edu.au"

/** Settings -> Canvas. */
@Composable
fun CanvasScreen() {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        CanvasConnectSection()
    }
}

/**
 * Connecting the user's Canvas: the address they sign in at, and a personal access token made
 * under Account -> Settings -> Approved Integrations. Walks them there step by step - the
 * "Open Canvas settings" button lands on that page directly once the address is filled in.
 *
 * Also the onboarding step (OnboardingFlow), so it fits inside someone else's scrolling column.
 */
@Composable
fun CanvasConnectSection() {
    val status by CanvasApiClient.status.collectAsState()
    var loadError by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        loadError = try {
            CanvasApiClient.refresh()
            null
        } catch (e: IOException) {
            "Couldn't check your Canvas connection - is Tailscale on?"
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        val current = status
        when {
            current?.connected == true -> Connected(current)
            current == null && loadError == null -> Row(
                Modifier.fillMaxWidth().padding(24.dp),
                horizontalArrangement = Arrangement.Center,
            ) { CircularProgressIndicator(Modifier.size(24.dp), strokeWidth = 2.dp) }
            else -> {
                loadError?.let { Detail(it, error = true) }
                ConnectForm()
            }
        }
    }
}

@Composable
private fun Connected(status: CanvasApiClient.Status) {
    val scope = rememberCoroutineScope()
    var confirm by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    Block {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Default.CheckCircle, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    status.canvasUserName?.let { "Connected as $it" } ?: "Canvas connected",
                    style = MaterialTheme.typography.titleMedium,
                )
                status.host?.let { Detail(it) }
            }
        }
        Detail(
            "Ask things like “what's due this week?”, “how did I go on my assignment?”, " +
                "“what's my next tutorial about?” or “does my lab have a prelab?”",
            modifier = Modifier.padding(top = 12.dp),
        )
    }

    Block {
        Text("Disconnect Canvas", style = MaterialTheme.typography.titleMedium)
        Detail(
            "Deletes the token from the Nova server. To cancel it completely, also delete it in " +
                "Canvas under Account → Settings → Approved Integrations."
        )
        OutlinedButton(
            onClick = { confirm = true },
            modifier = Modifier.padding(top = 12.dp),
            border = BorderStroke(1.dp, MaterialTheme.colorScheme.error.copy(alpha = 0.6f)),
            colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
        ) { Text("Disconnect") }
        error?.let { Detail(it, error = true, modifier = Modifier.padding(top = 8.dp)) }
    }

    if (confirm) {
        AlertDialog(
            onDismissRequest = { confirm = false },
            title = { Text("Disconnect Canvas?") },
            text = { Text("Nova won't be able to see your deadlines, marks or course materials.") },
            confirmButton = {
                TextButton(onClick = {
                    confirm = false
                    scope.launch {
                        error = try {
                            CanvasApiClient.disconnect()
                            null
                        } catch (e: IOException) {
                            "Couldn't disconnect - check your connection and try again."
                        }
                    }
                }) { Text("Disconnect", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { confirm = false }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun ConnectForm() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val focusManager = LocalFocusManager.current
    var address by remember { mutableStateOf(DEFAULT_CANVAS_ADDRESS) }
    var token by remember { mutableStateOf("") }
    var showToken by remember { mutableStateOf(false) }
    var connecting by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    // "canvas.uni.edu.au", "https://canvas.uni.edu.au/courses/1" -> the host alone.
    val host = address.trim().removePrefix("https://").removePrefix("http://").substringBefore('/')
    val hostLooksRight = host.contains('.') && host.none { it.isWhitespace() }

    Block {
        Text("Connect your Canvas", style = MaterialTheme.typography.titleMedium)
        Detail(
            "Nova can then tell you what's due, how you went on assessments, and what your " +
                "classes cover - “what's my next tutorial about?”. It only reads: Nova never " +
                "submits, posts or changes anything in Canvas."
        )
    }

    Block {
        Step(1, "Your Canvas address")
        Detail("The address you open Canvas at. Filled in for ANU - change it if yours is different.")
        OutlinedTextField(
            value = address,
            onValueChange = { address = it.take(200); error = null },
            placeholder = { Text("canvas.youruni.edu.au") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next),
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
        )

        Step(2, "Make an access token in Canvas", Modifier.padding(top = 20.dp))
        StepLine("Open Canvas and sign in - the button below goes straight to your settings.")
        StepLine("Or in Canvas: tap Account (your picture, top left), then Settings.")
        StepLine("Scroll down to Approved Integrations and tap + New Access Token.")
        StepLine("For Purpose, type “Nova”. Leave the expiry blank, or set it to the end of semester.")
        StepLine("Tap Generate Token, then copy the token. Canvas only shows it once.")
        OutlinedButton(
            onClick = {
                runCatching {
                    context.startActivity(
                        Intent(Intent.ACTION_VIEW, Uri.parse("https://$host/profile/settings"))
                    )
                }
            },
            enabled = hostLooksRight,
            modifier = Modifier.padding(top = 12.dp),
        ) {
            Icon(Icons.AutoMirrored.Filled.OpenInNew, contentDescription = null, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text("Open Canvas settings")
        }
        Detail(
            "Don't see + New Access Token? Some universities turn it off - ask your uni's IT help desk.",
            modifier = Modifier.padding(top = 8.dp),
        )

        Step(3, "Paste the token here", Modifier.padding(top = 20.dp))
        OutlinedTextField(
            value = token,
            onValueChange = { token = it.trim().take(200); error = null },
            label = { Text("Access token") },
            singleLine = true,
            visualTransformation = if (showToken) VisualTransformation.None else PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Password,
                autoCorrectEnabled = false,
                imeAction = ImeAction.Done,
            ),
            keyboardActions = KeyboardActions(onDone = { focusManager.clearFocus() }),
            trailingIcon = {
                IconButton(onClick = { showToken = !showToken }) {
                    Icon(
                        if (showToken) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                        contentDescription = if (showToken) "Hide token" else "Show token",
                    )
                }
            },
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
        )

        error?.let { Detail(it, error = true, modifier = Modifier.padding(top = 8.dp)) }

        Button(
            onClick = {
                focusManager.clearFocus()
                connecting = true
                error = null
                scope.launch {
                    error = try {
                        CanvasApiClient.connect(address, token)
                        null
                    } catch (e: CanvasApiClient.CanvasException) {
                        e.message
                    } catch (e: IOException) {
                        "Couldn't reach the Nova server - check your connection and try again."
                    } finally {
                        connecting = false
                    }
                }
            },
            enabled = hostLooksRight && token.length >= 20 && !connecting,
            modifier = Modifier.fillMaxWidth().padding(top = 16.dp),
        ) {
            if (connecting) {
                CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
            } else {
                Text("Connect")
            }
        }
        Detail(
            "The token is stored encrypted on your Nova server and only used to read Canvas when " +
                "you ask. You can disconnect here at any time.",
            modifier = Modifier.padding(top = 12.dp),
        )
    }
}

@Composable
private fun Step(number: Int, title: String, modifier: Modifier = Modifier) {
    Text(
        "$number. $title",
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.SemiBold,
        modifier = modifier,
    )
}

@Composable
private fun StepLine(text: String) {
    Row(Modifier.padding(top = 6.dp)) {
        Text("•", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.width(8.dp))
        Text(text, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun Block(content: @Composable ColumnScope.() -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(Modifier.padding(16.dp), content = content)
    }
}

@Composable
private fun Detail(text: String, modifier: Modifier = Modifier, error: Boolean = false) {
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = if (error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = modifier.padding(top = 2.dp),
    )
}
