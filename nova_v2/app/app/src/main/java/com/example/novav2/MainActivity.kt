package com.example.novav2

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.mutableStateOf
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.example.novav2.auth.AuthRepository
import com.example.novav2.auth.SessionState
import com.example.novav2.ble.NovaDevicePairing
import com.example.novav2.notes.NotesRepository
import com.example.novav2.service.NovaDeviceService
import com.example.novav2.service.SignalMonitorService
import com.example.novav2.state.ReminderScheduler
import com.example.novav2.ui.NovaApp
import com.example.novav2.ui.theme.NovaTheme
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* no-op either way */ }

    // singleTask (see manifest) means the power-button assist gesture reuses this instance via
    // onNewIntent() instead of creating a new one, so a plain intent.action check in onCreate
    // would miss that case - this flag is how NovaApp() jumps back to Voice when it fires.
    private val assistRequested = mutableStateOf(false)

    // Set alongside assistRequested when AssistTrampolineActivity already found Nova's own UI in
    // the foreground (see EXTRA_AUTO_LISTEN there) - tells VoiceScreen to trigger its mic button
    // itself rather than just landing on the Voice tab and waiting for a manual tap.
    private val autoListenRequested = mutableStateOf(false)

    // Set when a reminder notification opened the app (ReminderNotifier's content intent).
    private val remindersRequested = mutableStateOf(false)

    // Set when a notes notification opened the app (NoteNotifier) - a note id, or "" for the tab.
    private val noteRequested = mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        // The ambient loop posts to the server as the signed-in user, so it runs only while
        // someone is signed in: started on sign-in, stopped on sign-out (AuthRepository also
        // stops it when a session dies in the background).
        lifecycleScope.launch {
            AuthRepository.state.map { it is SessionState.SignedIn }.distinctUntilChanged().collect { signedIn ->
                val monitor = Intent(this@MainActivity, SignalMonitorService::class.java)
                if (signedIn) ContextCompat.startForegroundService(this@MainActivity, monitor)
                else stopService(monitor)
            }
        }
        // NovaDeviceService is otherwise only ever started once, from DeviceScreen's
        // pairing button - nothing else restarts it, so a process death (app swiped
        // away, low memory, ...) silently drops the BLE connection until the app is
        // reopened. startForegroundService is safe to call even if it's already
        // running (just redelivers onStartCommand), so this is a no-op most of the
        // time and a real restart the rest of the time.
        if (NovaDevicePairing.isPaired(this)) {
            ContextCompat.startForegroundService(this, Intent(this, NovaDeviceService::class.java))
        }
        // Every Run from Android Studio force-stops the app, and a force-stop cancels all of
        // its alarms - re-derive the reminder alarm from the table on every start.
        ReminderScheduler.reconcileAsync(this)
        remindersRequested.value = intent?.action == ACTION_OPEN_REMINDERS
        noteRequested.value = noteFrom(intent)
        // Anything captured offline since the app last ran goes up as soon as there's a network.
        NotesRepository.scheduleOutboxDrain(this)

        setContent {
            NovaTheme {
                NovaApp(
                    assistRequested = assistRequested,
                    autoListenRequested = autoListenRequested,
                    remindersRequested = remindersRequested,
                    noteRequested = noteRequested,
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (intent.action == ACTION_OPEN_REMINDERS) remindersRequested.value = true
        noteFrom(intent)?.let { noteRequested.value = it }
        if (intent.action == Intent.ACTION_ASSIST) {
            assistRequested.value = true
            if (intent.getBooleanExtra(AssistTrampolineActivity.EXTRA_AUTO_LISTEN, false)) {
                autoListenRequested.value = true
            }
        }
    }

    private fun noteFrom(intent: Intent?): String? =
        if (intent?.action == ACTION_OPEN_NOTE) intent.getStringExtra(EXTRA_NOTE_ID).orEmpty() else null

    companion object {
        /** A reminder notification's tap - opens the Reminders tab. */
        const val ACTION_OPEN_REMINDERS = "com.example.novav2.action.OPEN_REMINDERS"

        /** A notes notification's tap - opens [EXTRA_NOTE_ID], or the Notes tab without one. */
        const val ACTION_OPEN_NOTE = "com.example.novav2.action.OPEN_NOTE"
        const val EXTRA_NOTE_ID = "note_id"
    }
}
