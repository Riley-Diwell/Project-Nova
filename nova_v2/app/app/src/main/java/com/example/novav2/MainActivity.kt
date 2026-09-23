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
import com.example.novav2.ble.NovaDevicePairing
import com.example.novav2.service.NovaDeviceService
import com.example.novav2.service.SignalMonitorService
import com.example.novav2.ui.NovaApp
import com.example.novav2.ui.theme.NovaTheme

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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        ContextCompat.startForegroundService(this, Intent(this, SignalMonitorService::class.java))
        // NovaDeviceService is otherwise only ever started once, from DeviceScreen's
        // pairing button - nothing else restarts it, so a process death (app swiped
        // away, low memory, ...) silently drops the BLE connection until the app is
        // reopened. startForegroundService is safe to call even if it's already
        // running (just redelivers onStartCommand), so this is a no-op most of the
        // time and a real restart the rest of the time.
        if (NovaDevicePairing.isPaired(this)) {
            ContextCompat.startForegroundService(this, Intent(this, NovaDeviceService::class.java))
        }

        setContent {
            NovaTheme {
                NovaApp(assistRequested = assistRequested, autoListenRequested = autoListenRequested)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (intent.action == Intent.ACTION_ASSIST) {
            assistRequested.value = true
            if (intent.getBooleanExtra(AssistTrampolineActivity.EXTRA_AUTO_LISTEN, false)) {
                autoListenRequested.value = true
            }
        }
    }
}
