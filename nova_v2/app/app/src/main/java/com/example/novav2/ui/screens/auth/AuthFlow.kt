package com.example.novav2.ui.screens.auth

import androidx.activity.compose.BackHandler
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.novav2.viewmodel.SessionViewModel

/** What a signed-out phone shows instead of the app: Welcome, then sign in or sign up. */
@Composable
fun AuthFlow(viewModel: SessionViewModel = viewModel()) {
    var welcomed by rememberSaveable { mutableStateOf(false) }
    if (!welcomed) {
        WelcomeScreen(onContinue = { welcomed = true })
    } else {
        BackHandler { welcomed = false }
        SignInScreen(viewModel = viewModel, onBack = { welcomed = false })
    }
}
