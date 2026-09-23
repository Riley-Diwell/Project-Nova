package com.example.novav2.navigation

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bluetooth
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Hub
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Sensors
import androidx.compose.material.icons.filled.Tune
import androidx.compose.ui.graphics.vector.ImageVector

const val ONBOARDING_ROUTE = "onboarding"

sealed class NovaDestination(val route: String, val label: String, val icon: ImageVector) {
    data object Dashboard : NovaDestination("dashboard", "Dashboard", Icons.Default.Home)
    data object Voice : NovaDestination("voice", "Voice", Icons.Default.Mic)
    data object State : NovaDestination("state", "State", Icons.Default.Sensors)
    data object Gain : NovaDestination("gain", "Gain", Icons.Default.Tune)
    data object Knowledge : NovaDestination("knowledge", "Map", Icons.Default.Hub)
    data object Audit : NovaDestination("audit", "Audit", Icons.Default.History)
    data object Device : NovaDestination("device", "Device", Icons.Default.Bluetooth)
    data object Settings : NovaDestination("settings", "Settings", Icons.Default.Person)
}

// Dashboard is still hidden from the bottom nav - the screen/route exists, just not linked here
// yet. State, Gain, and Device are reachable from within SettingsScreen instead of the bottom
// nav (advanced/infrequent screens - pairing, per-tool gain tuning, raw signal state - not
// everyday destinations); their NovaDestination routes are still registered in NovaApp's NavHost,
// SettingsScreen just navigates to them directly rather than them getting their own tab.
val bottomNavDestinations = listOf(
//    NovaDestination.Dashboard
    NovaDestination.Voice,
    NovaDestination.Knowledge,
    NovaDestination.Audit,
    NovaDestination.Settings
)