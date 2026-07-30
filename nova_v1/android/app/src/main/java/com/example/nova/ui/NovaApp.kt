package com.example.nova.ui

import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.example.nova.model.UserProfile
import com.example.nova.navigation.NovaDestination
import com.example.nova.navigation.bottomNavDestinations
import com.example.nova.ui.screens.DashboardScreen
import com.example.nova.ui.screens.DeviceScreen
import com.example.nova.ui.screens.GainScreen
import com.example.nova.ui.screens.KnowledgeMapScreen
import com.example.nova.ui.screens.OnboardingScreen
import com.example.nova.ui.screens.SettingsScreen
import com.example.nova.ui.screens.StateScreen
import com.example.nova.ui.screens.VoiceScreen

@Composable
fun NovaApp() {
    // TODO: re-enable onboarding gate - skipped for now to speed up dev iteration.
    var onboardingComplete by rememberSaveable { mutableStateOf(true) }
    var userName by rememberSaveable { mutableStateOf("") }
    var dailyGoalMinutes by rememberSaveable { mutableIntStateOf(120) }
    var deviceConnected by rememberSaveable { mutableStateOf(false) }

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

    Scaffold(
        bottomBar = {
            NavigationBar {
                bottomNavDestinations.forEach { destination ->
                    NavigationBarItem(
                        selected = currentRoute == destination.route,
                        onClick = {
                            navController.navigate(destination.route) {
                                popUpTo(navController.graph.findStartDestination().id) {
                                    saveState = true
                                }
                                launchSingleTop = true
                                restoreState = true
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
                VoiceScreen()
            }
            composable(NovaDestination.State.route) {
                StateScreen()
            }
            composable(NovaDestination.Gain.route) {
                GainScreen()
            }
            composable(NovaDestination.Knowledge.route) {
                KnowledgeMapScreen()
            }
            composable(NovaDestination.Device.route) {
                DeviceScreen(
                    connected = deviceConnected,
                    onToggleConnected = { deviceConnected = !deviceConnected }
                )
            }
            composable(NovaDestination.Settings.route) {
                SettingsScreen(
                    profile = profile,
                    onNameChange = { userName = it },
                    onGoalChange = { dailyGoalMinutes = it }
                )
            }
        }
    }
}
