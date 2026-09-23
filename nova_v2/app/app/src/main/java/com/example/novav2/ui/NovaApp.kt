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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.navigation.NavController
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.example.novav2.model.UserProfile
import com.example.novav2.navigation.NovaDestination
import com.example.novav2.navigation.bottomNavDestinations
import com.example.novav2.ui.screens.AuditLogScreen
import com.example.novav2.ui.screens.DashboardScreen
import com.example.novav2.ui.screens.DeviceScreen
import com.example.novav2.ui.screens.GainScreen
import com.example.novav2.ui.screens.KnowledgeMapScreen
import com.example.novav2.ui.screens.OnboardingScreen
import com.example.novav2.ui.screens.SettingsScreen
import com.example.novav2.ui.screens.StateScreen
import com.example.novav2.ui.screens.VoiceScreen

@Composable
fun NovaApp(
    assistRequested: MutableState<Boolean> = mutableStateOf(false),
    autoListenRequested: MutableState<Boolean> = mutableStateOf(false),
) {
    // TODO: re-enable onboarding gate - skipped for now to speed up dev iteration.
    var onboardingComplete by rememberSaveable { mutableStateOf(true) }
    var userName by rememberSaveable { mutableStateOf("") }
    var dailyGoalMinutes by rememberSaveable { mutableIntStateOf(120) }

    val profile = UserProfile(name = userName, dailyGoalMinutes = dailyGoalMinutes)

    if (!onboardingComplete) {
        OnboardingScreen(
            profile = profile,
            onNameChange = { userName = it },
            onGoalChange = { dailyGoalMinutes = it },
            onFinish = { onboardingComplete = true }
        )
        return
    }

    val navController = rememberNavController()
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route

    LaunchedEffect(assistRequested.value) {
        if (assistRequested.value) {
            navController.navigate(NovaDestination.Voice.route) {
                popUpTo(navController.graph.findStartDestination().id) { saveState = false }
                launchSingleTop = true
            }
            assistRequested.value = false
        }
    }

    Scaffold(
        contentWindowInsets = WindowInsets.systemBars.only(WindowInsetsSides.Horizontal + WindowInsetsSides.Top),
        bottomBar = {
            NavigationBar {
                bottomNavDestinations.forEach { destination ->
                    NavigationBarItem(
                        selected = currentRoute == destination.route,
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
                )
            }
            composable(NovaDestination.State.route) {
                SettingsSubScreen(NovaDestination.State, navController) { StateScreen() }
            }
            composable(NovaDestination.Gain.route) {
                SettingsSubScreen(NovaDestination.Gain, navController) { GainScreen() }
            }
            composable(NovaDestination.Knowledge.route) {
                KnowledgeMapScreen()
            }
            composable(NovaDestination.Audit.route) {
                AuditLogScreen()
            }
            composable(NovaDestination.Device.route) {
                SettingsSubScreen(NovaDestination.Device, navController) { DeviceScreen() }
            }
            composable(NovaDestination.Settings.route) {
                SettingsScreen(navController)
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
                title = { Text(destination.label) },
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
