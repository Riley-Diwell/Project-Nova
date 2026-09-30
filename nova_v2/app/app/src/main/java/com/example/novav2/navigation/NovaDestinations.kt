package com.example.novav2.navigation

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.Bluetooth
import androidx.compose.material.icons.filled.Checklist
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Hub
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.automirrored.filled.StickyNote2
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.School
import androidx.compose.material.icons.filled.Sensors
import androidx.compose.material.icons.filled.Tune
import androidx.compose.ui.graphics.vector.ImageVector

sealed class NovaDestination(val route: String, val label: String, val icon: ImageVector) {
    data object Dashboard : NovaDestination("dashboard", "Dashboard", Icons.Default.Home)
    data object Voice : NovaDestination("voice", "Voice", Icons.Default.Mic)
    data object Reminders : NovaDestination("reminders", "Reminders", Icons.Default.Checklist)
    data object Notes : NovaDestination("notes", "Notes", Icons.AutoMirrored.Filled.StickyNote2)
    data object NotesSettings : NovaDestination("notes_settings", "Notes", Icons.AutoMirrored.Filled.StickyNote2)
    data object State : NovaDestination("state", "State", Icons.Default.Sensors)
    data object Gain : NovaDestination("gain", "Gain", Icons.Default.Tune)
    data object Knowledge : NovaDestination("knowledge", "Map", Icons.Default.Hub)
    data object Audit : NovaDestination("audit", "Audit", Icons.Default.History)
    data object Device : NovaDestination("device", "Device", Icons.Default.Bluetooth)
    data object Settings : NovaDestination("settings", "Settings", Icons.Default.Person)
    /** Settings -> Your profile: the onboarding answers, editable. */
    data object Profile : NovaDestination("profile", "Profile", Icons.Default.AccountCircle)
    /** Settings -> Canvas: connect the user's uni Canvas with an access token. */
    data object Canvas : NovaDestination("canvas", "Canvas", Icons.Default.School)
}

// Dashboard is still hidden from the bottom nav - the screen/route exists, just not linked here
// yet. State, Gain, and Device are reachable from within SettingsScreen instead of the bottom
// nav (advanced/infrequent screens - pairing, per-tool gain tuning, raw signal state - not
// everyday destinations); their NovaDestination routes are still registered in NovaApp's NavHost,
// SettingsScreen just navigates to them directly rather than them getting their own tab.
val bottomNavDestinations = listOf(
//    NovaDestination.Dashboard
    NovaDestination.Voice,
    // An everyday destination, unlike Gain/State - reminders are something you check.
    NovaDestination.Reminders,
    // The Notes tab. The bar holds five at most, so Audit moved
    // into Settings next to Gain/State/Device - reviewing past actions is occasional, notes
    // and reminders are daily.
    NovaDestination.Notes,
    NovaDestination.Knowledge,
    NovaDestination.Settings
)

/** A note's detail view, pushed on top of the Notes tab. */
const val NOTE_DETAIL_ROUTE = "notes/{noteId}"
fun noteDetailRoute(noteId: String) = "notes/$noteId"