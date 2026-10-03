package com.example.novav2.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBars
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.navigation.NavController
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.example.novav2.BuildConfig
import com.example.novav2.auth.AuthRepository
import com.example.novav2.auth.SessionState
import com.example.novav2.model.UserProfile
import com.example.novav2.profile.ProfileRepository
import com.example.novav2.navigation.NOTE_DETAIL_ROUTE
import com.example.novav2.navigation.NovaDestination
import com.example.novav2.navigation.noteDetailRoute
import com.example.novav2.navigation.bottomNavDestinations
import com.example.novav2.ui.screens.AuditLogScreen
import com.example.novav2.ui.screens.DashboardScreen
import com.example.novav2.ui.screens.DeviceScreen
import com.example.novav2.ui.screens.GainScreen
import com.example.novav2.ui.screens.knowledgemap.KnowledgeMapScreen
import com.example.novav2.ui.screens.NoteDetailScreen
import com.example.novav2.ui.screens.NotesScreen
import com.example.novav2.ui.screens.NotesSettingsScreen
import com.example.novav2.ui.screens.CanvasScreen
import com.example.novav2.ui.screens.ProfileScreen
import com.example.novav2.ui.screens.RemindersScreen
import com.example.novav2.ui.screens.SETTINGS_SUBSCREEN_ROUTES
import com.example.novav2.ui.screens.SettingsScreen
import com.example.novav2.ui.screens.settingsTitle
import com.example.novav2.ui.screens.StateScreen
import com.example.novav2.ui.screens.VoiceScreen
import com.example.novav2.ui.screens.auth.AuthFlow
import com.example.novav2.ui.screens.onboarding.OnboardingFlow
import com.example.novav2.ui.screens.onboarding.ProfileLoading
import com.example.novav2.ui.screens.onboarding.ProfileUnavailable
import com.example.novav2.ui.components.clearFocusOnTap

@Composable
fun NovaApp(
    assistRequested: MutableState<Boolean> = mutableStateOf(false),
    autoListenRequested: MutableState<Boolean> = mutableStateOf(false),
    remindersRequested: MutableState<Boolean> = mutableStateOf(false),
    /** A note to open - set when a notes notification opened the app ("" = just the tab). */
    noteRequested: MutableState<String?> = mutableStateOf(null),
) {
    // Signed out: Welcome and sign-in instead of the app.
    val session by AuthRepository.state.collectAsState()
    if (session is SessionState.SignedOut) {
        AuthFlow()
        return
    }

    // Signed in but not onboarded: onboarding first. The
    // profile is cached, so an offline start of an onboarded account goes straight in. A debug
    // build can skip it with SKIP_ONBOARDING=true in local.properties.
    val profileState by ProfileRepository.state.collectAsState()
    val skippedProfile by ProfileRepository.skippedThisRun.collectAsState()
    if (!BuildConfig.SKIP_ONBOARDING && !skippedProfile) {
        when (val p = profileState) {
            ProfileRepository.ProfileState.Loading -> { ProfileLoading(); return }
            is ProfileRepository.ProfileState.Unavailable -> { ProfileUnavailable(p.message); return }
            is ProfileRepository.ProfileState.Ready -> if (p.profile?.needsOnboarding != false) {
                OnboardingFlow()
                return
            }
        }
    }
    val accountProfile = (profileState as? ProfileRepository.ProfileState.Ready)?.profile
    val profile = UserProfile(name = accountProfile?.displayName.orEmpty())

    val navController = rememberNavController()
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route
    // Pushed screens keep the tab they were opened from lit, instead of leaving no tab selected.
    val selectedTabRoute = when (currentRoute) {
        NOTE_DETAIL_ROUTE -> navController.previousBackStackEntry?.destination?.route
            ?.takeIf { route -> bottomNavDestinations.any { it.route == route } }
            ?: NovaDestination.Notes.route
        in SETTINGS_SUBSCREEN_ROUTES -> NovaDestination.Settings.route
        else -> currentRoute
    }

    LaunchedEffect(assistRequested.value) {
        if (assistRequested.value) {
            navController.navigate(NovaDestination.Voice.route) {
                popUpTo(navController.graph.findStartDestination().id) { saveState = false }
                launchSingleTop = true
            }
            assistRequested.value = false
        }
    }

    // Tapping a reminder notification lands on the Reminders tab.
    LaunchedEffect(remindersRequested.value) {
        if (remindersRequested.value) {
            navController.navigate(NovaDestination.Reminders.route) {
                popUpTo(navController.graph.findStartDestination().id)
                launchSingleTop = true
            }
            remindersRequested.value = false
        }
    }

    // Tapping a notes notification lands on the note (or the Notes tab).
    LaunchedEffect(noteRequested.value) {
        val noteId = noteRequested.value ?: return@LaunchedEffect
        navController.navigate(NovaDestination.Notes.route) {
            popUpTo(navController.graph.findStartDestination().id)
            launchSingleTop = true
        }
        if (noteId.isNotEmpty()) navController.navigate(noteDetailRoute(noteId))
        noteRequested.value = null
    }

    Scaffold(
        modifier = Modifier.clearFocusOnTap(),
        contentWindowInsets = WindowInsets.systemBars.only(WindowInsetsSides.Horizontal + WindowInsetsSides.Top),
        bottomBar = {
            NavigationBar {
                bottomNavDestinations.forEach { destination ->
                    NavigationBarItem(
                        selected = selectedTabRoute == destination.route,
                        onClick = {
                            navController.navigate(destination.route) {
                                // No saveState/restoreState: those would let a settings
                                // submenu (Gain/Device/State) hang around underneath the
                                // Settings tab and reappear when you tab back to it - each
                                // tab should always land on its own root.
                                popUpTo(navController.graph.findStartDestination().id)
                                launchSingleTop = true
                            }
                        },
                        icon = { Icon(destination.icon, contentDescription = destination.label) },
                        label = { Text(destination.label) }
                    )
                }
            }
        }
    ) { innerPadding ->
        NavHost(
            navController = navController,
            startDestination = NovaDestination.Voice.route,
            modifier = Modifier.padding(innerPadding)
        ) {
            composable(NovaDestination.Dashboard.route) {
                DashboardScreen(profile = profile)
            }
            composable(NovaDestination.Voice.route) {
                VoiceScreen(
                    bottomBarHeight = innerPadding.calculateBottomPadding(),
                    autoListenRequested = autoListenRequested,
                    onOpenNote = { navController.navigate(noteDetailRoute(it)) },
                )
            }
            composable(NovaDestination.Reminders.route) {
                RemindersScreen()
            }
            composable(NovaDestination.State.route) {
                SettingsSubScreen(NovaDestination.State, navController) { StateScreen() }
            }
            composable(NovaDestination.Gain.route) {
                SettingsSubScreen(NovaDestination.Gain, navController) { GainScreen() }
            }
            composable(NovaDestination.Notes.route) {
                NotesScreen(onOpenNote = { navController.navigate(noteDetailRoute(it)) })
            }
            composable(NOTE_DETAIL_ROUTE) { entry ->
                val noteId = entry.arguments?.getString("noteId").orEmpty()
                SettingsSubScreen(NovaDestination.Notes, navController) {
                    NoteDetailScreen(noteId, onClosed = { navController.popBackStack() })
                }
            }
            composable(NovaDestination.NotesSettings.route) {
                SettingsSubScreen(NovaDestination.NotesSettings, navController) { NotesSettingsScreen() }
            }
            composable(NovaDestination.Knowledge.route) {
                KnowledgeMapScreen(onOpenNote = { navController.navigate(noteDetailRoute(it)) })
            }
            composable(NovaDestination.Audit.route) {
                SettingsSubScreen(NovaDestination.Audit, navController) { AuditLogScreen() }
            }
            composable(NovaDestination.Device.route) {
                SettingsSubScreen(NovaDestination.Device, navController) { DeviceScreen() }
            }
            composable(NovaDestination.Settings.route) {
                SettingsScreen(navController)
            }
            composable(NovaDestination.Profile.route) {
                SettingsSubScreen(NovaDestination.Profile, navController) { ProfileScreen() }
            }
            composable(NovaDestination.Canvas.route) {
                SettingsSubScreen(NovaDestination.Canvas, navController) { CanvasScreen() }
            }
        }
    }
}

/** Wraps a settings submenu (Device/Gain/State - see SETTINGS_SUBSCREENS in SettingsScreen.kt)
 * with a top bar and back button, since these are pushed on top of the Settings tab rather than
 * getting their own bottom nav entry and would otherwise have no visible way back besides the
 * system gesture. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SettingsSubScreen(
    destination: NovaDestination,
    navController: NavController,
    content: @Composable () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(destination.settingsTitle) },
                navigationIcon = {
                    IconButton(onClick = { navController.popBackStack() }) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                }
            )
        }
    ) { innerPadding ->
        Box(modifier = Modifier.padding(innerPadding)) {
            content()
        }
    }
}
