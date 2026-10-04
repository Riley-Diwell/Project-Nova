package com.example.novav2.ui.screens

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.automirrored.filled.StickyNote2
import androidx.compose.material.icons.automirrored.filled.VolumeOff
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.DeleteSweep
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isShiftPressed
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.novav2.model.ChatMessage
import com.example.novav2.navigation.NovaDestination
import com.example.novav2.network.SavedAction
import com.example.novav2.state.CalendarWriter
import com.example.novav2.viewmodel.ChatViewModel
import com.example.novav2.viewmodel.VoiceState
import com.example.novav2.ui.components.EmptyState
import com.example.novav2.ui.components.ScreenHeader

/** The tab a "Saved to …" chip leads to - its icon is the chip's icon too, so the chip and the
 * bottom bar point at the same place. */
private fun SavedAction.destination(): NovaDestination = when (kind) {
    SavedAction.Kind.NOTE -> NovaDestination.Notes
    SavedAction.Kind.MEMORY -> NovaDestination.Knowledge
    SavedAction.Kind.REMINDER -> NovaDestination.Reminders
}

private fun SavedAction.chipPrefix(): String = when (kind) {
    SavedAction.Kind.NOTE -> "Note"
    SavedAction.Kind.MEMORY -> "Remembered"
    SavedAction.Kind.REMINDER -> "Reminder"
}

@Composable
private fun MessageBubble(message: ChatMessage) {
    Box(
        modifier = Modifier.fillMaxWidth(),
        contentAlignment = if (message.fromUser) Alignment.CenterEnd else Alignment.CenterStart,
    ) {
        Surface(
            color = if (message.fromUser) {
                MaterialTheme.colorScheme.primary
            } else {
                MaterialTheme.colorScheme.surfaceVariant
            },
            shape = RoundedCornerShape(
                topStart = 16.dp,
                topEnd = 16.dp,
                bottomStart = if (message.fromUser) 16.dp else 4.dp,
                bottomEnd = if (message.fromUser) 4.dp else 16.dp,
            ),
            modifier = Modifier.fillMaxWidth(0.82f).wrapContentWidth(
                if (message.fromUser) Alignment.End else Alignment.Start,
            ),
        ) {
            Text(
                text = message.text,
                style = MaterialTheme.typography.bodyLarge,
                color = if (message.fromUser) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
            )
        }
    }
}

/**
 * A turn in flight or something needing saying that isn't a real reply (listening, sending, a
 * recognizer miss…) - the same bubble shape a finished reply would use, so the transcript never
 * has a second, differently-styled place to look for what's happening. Replaces what used to be
 * a fixed "Nova is thinking…" bubble plus a separate caption below the chat - one indicator,
 * driven by whatever `statusText` currently says. Defaults to Nova's side; `fromUser` moves it
 * to the user's side (e.g. "Listening…", which describes what the user is doing, not Nova).
 */
@Composable
private fun PendingStatusBubble(text: String, fromUser: Boolean = false) {
    Box(
        modifier = Modifier.fillMaxWidth(),
        contentAlignment = if (fromUser) Alignment.CenterEnd else Alignment.CenterStart,
    ) {
        Surface(
            color = if (fromUser) {
                MaterialTheme.colorScheme.primary
            } else {
                MaterialTheme.colorScheme.surfaceVariant
            },
            shape = RoundedCornerShape(
                topStart = 16.dp,
                topEnd = 16.dp,
                bottomStart = if (fromUser) 16.dp else 4.dp,
                bottomEnd = if (fromUser) 4.dp else 16.dp,
            ),
        ) {
            Text(
                text = text,
                style = MaterialTheme.typography.bodyMedium.copy(fontStyle = FontStyle.Italic),
                // Muted grey on the blue user-side bubble was close to unreadable.
                color = if (fromUser) MaterialTheme.colorScheme.onPrimary.copy(alpha = 0.85f)
                else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
            )
        }
    }
}

/**
 * DESIGN.md §5.1/§5.3: text or SpeechRecognizer input -> POST /event -> TextToSpeech, rendered
 * as a message thread (user bubbles on the right, Nova's replies on the left) rather than a
 * single last-turn readout, so the reply is always visible even before/without TTS finishing.
 *
 * The turn itself lives in [ChatViewModel] (Activity-scoped), so a message sent here keeps going
 * - and its "Thinking…" bubble is still showing on return - if the user switches tabs meanwhile.
 * This composable only holds what needs an Activity: the permission prompts.
 */
@OptIn(ExperimentalLayoutApi::class) // FlowRow, for the choice buttons
@Composable
fun VoiceScreen(
    bottomBarHeight: Dp = 0.dp,
    autoListenRequested: MutableState<Boolean> = mutableStateOf(false),
    onOpenNote: (String) -> Unit = {},
    /** Switches to another tab - where a "Saved to …" chip leads. */
    onOpenTab: (NovaDestination) -> Unit = {},
) {
    val context = LocalContext.current
    val listState = rememberLazyListState()
    val focusManager = LocalFocusManager.current
    val keyboardController = LocalSoftwareKeyboardController.current
    val density = LocalDensity.current

    val chatViewModel: ChatViewModel = viewModel(viewModelStoreOwner = context as ComponentActivity)
    val messages = chatViewModel.messages
    val voiceState = chatViewModel.voiceState
    val statusText = chatViewModel.statusText
    val pendingConfirmation = chatViewModel.pendingConfirmation
    val recallChips = chatViewModel.recallChips
    val savedChips = chatViewModel.savedChips

    var hasPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
        )
    }
    // Set only while waiting on WRITE_CALENDAR after the user has ALREADY said yes to a specific
    // deletion - holds just that one id so the launcher's callback has something to act on.
    var deleteAwaitingPermission by remember { mutableStateOf<Long?>(null) }

    // The status bubble is its own item after the last message, so it's the one to scroll to
    // while it's showing. Also runs on returning to the tab, landing on the latest turn.
    val lastIndex = messages.size - 1 + if (statusText.isNotBlank()) 1 else 0
    LaunchedEffect(lastIndex, voiceState) {
        if (lastIndex >= 0) listState.animateScrollToItem(lastIndex)
    }

    // Both calendar permissions in one prompt - see CalendarWriter.PERMISSIONS.
    val calendarPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        chatViewModel.onCalendarPermissionResult(granted = results.values.all { it })
    }
    // A reply can queue calendar writes while the user is on another tab - ask once they're
    // back here rather than popping a system prompt over an unrelated screen.
    val calendarAwaitingPermission =
        chatViewModel.pendingCalendarActions.isNotEmpty() || chatViewModel.pendingEditActions.isNotEmpty()
    LaunchedEffect(calendarAwaitingPermission) {
        if (calendarAwaitingPermission) calendarPermissionLauncher.launch(CalendarWriter.PERMISSIONS)
    }

    val deletePermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        val eventId = deleteAwaitingPermission
        deleteAwaitingPermission = null
        if (results.values.all { it } && eventId != null) {
            chatViewModel.deleteCalendarEvent(eventId)
        }
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        hasPermission = granted
        if (granted) {
            chatViewModel.startListening()
        } else {
            chatViewModel.showNotice("Microphone permission is required for voice input.")
        }
    }

    fun onMicClick() {
        if (!hasPermission) {
            permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        } else if (voiceState == VoiceState.LISTENING) {
            chatViewModel.finishListening()
        } else if (voiceState == VoiceState.IDLE) {
            chatViewModel.startListening()
        }
    }

    // The power-button assist gesture, fired while Nova's own UI was already in the foreground
    // (see AssistTrampolineActivity's EXTRA_AUTO_LISTEN) - reuses onMicClick() so it goes through
    // the exact same permission check/prompt a manual tap would, rather than assuming
    // RECORD_AUDIO is already granted. A no-op if a turn is already THINKING/SPEAKING, same as
    // the mic button being disabled then.
    LaunchedEffect(autoListenRequested.value) {
        if (autoListenRequested.value) {
            onMicClick()
            autoListenRequested.value = false
        }
    }

    // The hard gate for delete_calendar_event: shown for every queued deletion, one at a time,
    // regardless of gain or how the model phrased its speech. Nothing in CalendarWriter runs
    // until the user taps Delete here.
    chatViewModel.pendingDeleteConfirmations.firstOrNull()?.let { action ->
        AlertDialog(
            onDismissRequest = { chatViewModel.dismissDeleteConfirmation() },
            title = { Text("Delete this event?") },
            text = { Text("\"${action.title}\" will be removed from your calendar.") },
            confirmButton = {
                TextButton(onClick = {
                    chatViewModel.dismissDeleteConfirmation()
                    if (CalendarWriter.hasPermission(context)) {
                        chatViewModel.deleteCalendarEvent(action.eventId)
                    } else {
                        deleteAwaitingPermission = action.eventId
                        deletePermissionLauncher.launch(CalendarWriter.PERMISSIONS)
                    }
                }) { Text("Delete") }
            },
            dismissButton = {
                TextButton(onClick = { chatViewModel.dismissDeleteConfirmation() }) {
                    Text("Cancel")
                }
            },
        )
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .pointerInput(Unit) {
                detectTapGestures(onTap = {
                    keyboardController?.hide()
                    focusManager.clearFocus()
                })
            },
    ) {
        ScreenHeader(title = "Nova") {
            if (messages.isNotEmpty()) {
                TextButton(onClick = { chatViewModel.clearMessages() }) {
                    Icon(Icons.Default.DeleteSweep, contentDescription = null, Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Clear chat")
                }
            }
            // Whether replies are read out. Replies always land in the thread either way.
            IconButton(onClick = { chatViewModel.toggleMuted() }) {
                Icon(
                    if (chatViewModel.muted) Icons.AutoMirrored.Filled.VolumeOff else Icons.AutoMirrored.Filled.VolumeUp,
                    contentDescription = if (chatViewModel.muted) "Unmute replies" else "Mute replies",
                )
            }
        }
        // The empty state only when there's truly nothing to show - a status with no messages yet
        // goes through the LazyColumn below, so it sits where the first message would rather
        // than floating in the middle of the screen.
        if (messages.isEmpty() && statusText.isBlank()) {
            Box(modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 16.dp), contentAlignment = Alignment.Center) {
                EmptyState(
                    Icons.Default.GraphicEq,
                    "How can I help?",
                    "Tap the mic and speak, or type a message below.",
                )
            }
        } else {
            LazyColumn(
                state = listState,
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
                contentPadding = PaddingValues(vertical = 8.dp),
            ) {
                items(messages, key = { it.id }) { message ->
                    MessageBubble(message)
                }
                if (statusText.isNotBlank()) {
                    item(key = "status-indicator") {
                        PendingStatusBubble(statusText, fromUser = voiceState == VoiceState.LISTENING)
                    }
                }
            }
        }

        // Only while NOVA is talking, because that is the only moment it means
        // anything. Cutting it off is the user's stop control and, per
        // DESIGN.md §5.7, the turn's rejection - the one negative signal V1
        // collects, and one that costs no extra interaction.
        if (voiceState == VoiceState.SPEAKING) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.Start,
            ) {
                OutlinedButton(onClick = { chatViewModel.stopSpeaking() }) {
                    Icon(Icons.Default.Stop, contentDescription = null)
                    Spacer(Modifier.width(8.dp))
                    Text("Stop")
                }
            }
        }

        if (recallChips.isNotEmpty()) {
            Row(
                modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                recallChips.forEach { row ->
                    AssistChip(
                        onClick = { onOpenNote(row.id) },
                        label = {
                            Text(row.title, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.widthIn(max = 200.dp))
                        },
                        leadingIcon = { Icon(Icons.AutoMirrored.Filled.StickyNote2, null, Modifier.size(16.dp)) },
                    )
                }
            }
        }

        if (savedChips.isNotEmpty()) {
            Row(
                modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                savedChips.forEach { saved ->
                    val tab = saved.destination()
                    AssistChip(
                        onClick = {
                            val noteId = saved.noteId
                            if (noteId != null) onOpenNote(noteId) else onOpenTab(tab)
                        },
                        label = {
                            Text("${saved.chipPrefix()}: ${saved.text}", maxLines = 1,
                                overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 240.dp))
                        },
                        leadingIcon = { Icon(tab.icon, null, Modifier.size(16.dp)) },
                    )
                }
            }
        }

        // Quick replies for a dangling yes/no question (EventOut.confirmation) - voice and
        // typed "Other" answers still go through sendMessage/onMicClick exactly as before,
        // these buttons are just a shortcut into the same path.
        if (pendingConfirmation == "yes_no" && voiceState == VoiceState.IDLE) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedButton(onClick = { chatViewModel.sendMessage("Yes") }) { Text("Yes") }
                OutlinedButton(onClick = { chatViewModel.sendMessage("No") }) { Text("No") }
            }
        }
        // An ask_choice question: each label is sent word for word, which is the answer the
        // server is waiting for.
        val options = chatViewModel.pendingOptions
        if (pendingConfirmation == "choice" && options.isNotEmpty() && voiceState == VoiceState.IDLE) {
            FlowRow(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                options.forEach { option ->
                    OutlinedButton(onClick = { chatViewModel.sendMessage(option) }) { Text(option) }
                }
            }
        }

        // ime's bottom inset is measured from the true screen edge, which is below the app's
        // own bottom NavigationBar - but that bar doesn't move or shrink when the keyboard
        // opens (NavHost is already offset above it via Scaffold's innerPadding), it just gets
        // covered by the keyboard overlay. So the raw ime value bakes in that bar's height on
        // top of the keyboard's own height; only the portion of ime beyond bottomBarHeight is
        // actually eating into this content's own area and needs to be padded for here.
        val imeBottomPx = WindowInsets.ime.getBottom(density)
        val reservedBottomPx = with(density) { bottomBarHeight.roundToPx() }
        val extraKeyboardPadding = with(density) { (imeBottomPx - reservedBottomPx).coerceAtLeast(0).toDp() }

        HorizontalDivider()
        Surface(color = MaterialTheme.colorScheme.surfaceContainer) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(bottom = extraKeyboardPadding)
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(
                        value = chatViewModel.inputText,
                        onValueChange = { chatViewModel.inputText = it },
                        modifier = Modifier
                            .weight(1f)
                            .onPreviewKeyEvent { event ->
                                val isEnter = event.key == Key.Enter || event.key == Key.NumPadEnter
                                if (event.type == KeyEventType.KeyDown && isEnter && !event.isShiftPressed) {
                                    chatViewModel.sendDraft()
                                    true
                                } else {
                                    false
                                }
                            },
                        placeholder = {
                            Text(if (pendingConfirmation == "yes_no") "Yes, no, or something else…" else "Message Nova…")
                        },
                        maxLines = 4,
                        shape = RoundedCornerShape(24.dp),
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                        keyboardActions = KeyboardActions(onSend = { chatViewModel.sendDraft() }),
                    )
                    Spacer(Modifier.width(8.dp))
                    FilledIconButton(
                        onClick = { chatViewModel.sendDraft() },
                        // One turn at a time - the draft waits while a reply is on its way.
                        enabled = chatViewModel.inputText.isNotBlank() && voiceState != VoiceState.THINKING,
                        modifier = Modifier.size(48.dp),
                    ) {
                        Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send")
                    }
                }
                Spacer(Modifier.height(8.dp))
                FilledIconButton(
                    onClick = { onMicClick() },
                    enabled = voiceState == VoiceState.IDLE || voiceState == VoiceState.LISTENING,
                    shape = RoundedCornerShape(24.dp),
                    colors = IconButtonDefaults.filledIconButtonColors(
                        containerColor = if (voiceState == VoiceState.LISTENING) MaterialTheme.colorScheme.error
                        else MaterialTheme.colorScheme.primary,
                        contentColor = Color.White,
                        disabledContainerColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.4f),
                        disabledContentColor = Color.White.copy(alpha = 0.7f),
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(56.dp),
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            if (voiceState == VoiceState.LISTENING) Icons.Default.Stop else Icons.Default.Mic,
                            contentDescription = null,
                        )
                        Spacer(Modifier.width(8.dp))
                        Text(
                            when (voiceState) {
                                VoiceState.LISTENING -> "Tap to send"
                                VoiceState.THINKING -> "Thinking…"
                                VoiceState.SPEAKING -> "Speaking…"
                                VoiceState.IDLE -> "Tap to speak"
                            },
                            style = MaterialTheme.typography.labelLarge,
                        )
                    }
                }
            }
        }
    }
}
