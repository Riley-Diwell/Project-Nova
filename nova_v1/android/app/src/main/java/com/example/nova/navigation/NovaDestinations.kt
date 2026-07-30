package com.example.nova.navigation

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bluetooth
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
    data object Device : NovaDestination("device", "Device", Icons.Default.Bluetooth)
    data object Settings : NovaDestination("settings", "Profile", Icons.Default.Person)
}

// Dashboard and Profile are temporarily hidden from the bottom nav during the Phase 0/1 spike
// (DESIGN.md §7) - the screens/routes still exist, just not linked here for now.
val bottomNavDestinations = listOf(
//    NovaDestination.Dashboard
    NovaDestination.Voice,
    NovaDestination.State,
    NovaDestination.Gain,
    NovaDestination.Knowledge
//    NovaDestination.Device,
//    NovaDestination.Settings
)