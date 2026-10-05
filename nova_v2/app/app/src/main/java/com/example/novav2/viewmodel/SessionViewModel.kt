package com.example.novav2.viewmodel

import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.ViewModel
import com.example.novav2.auth.AuthRepository
import com.example.novav2.auth.SessionState
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/** Drives the sign-in screen and the Settings account section. */
class SessionViewModel : ViewModel() {
    val session: StateFlow<SessionState> = AuthRepository.state

    var busy by mutableStateOf(false)
        private set

    /** A message for the user after a failed attempt; cleared when they try again. */
    var error by mutableStateOf<String?>(null)
        private set

    /** Set after a sign-up that needs the email confirmed before signing in. */
    var notice by mutableStateOf<String?>(null)
        private set

    fun signIn(email: String, password: String) = attempt {
        AuthRepository.signIn(email, password)
    }

    fun signUp(email: String, password: String) = attempt {
        if (AuthRepository.signUp(email, password) == AuthRepository.SignUpResult.ConfirmEmail) {
            notice = "Check your email to confirm your account, then sign in."
        }
    }

    /** A sign-out waiting on the user: [unsent] changes couldn't be uploaded first. */
    data class BlockedSignOut(val everywhere: Boolean, val unsent: Int)

    var blockedSignOut by mutableStateOf<BlockedSignOut?>(null)
        private set

    /** [force] signs out even if unsent changes would be lost (after the user confirms). */
    fun signOut(everywhere: Boolean = false, force: Boolean = false) = attempt {
        blockedSignOut = null
        try {
            AuthRepository.signOut(everywhere, force)
        } catch (e: AuthRepository.UnsentChangesException) {
            blockedSignOut = BlockedSignOut(everywhere, e.count)
        }
    }

    /** Deletes the account for good; on success the app is back at sign-in. */
    fun deleteAccount(password: String) = attempt {
        AuthRepository.deleteAccount(password)
    }

    fun cancelSignOut() {
        blockedSignOut = null
    }

    fun clearMessages() {
        error = null
        notice = null
    }

    // In the app's scope, not viewModelScope: leaving Settings mid-sign-out mustn't cancel it
    // halfway (the ViewModel belongs to the Settings screen and goes when it does).
    private fun attempt(block: suspend () -> Unit) {
        if (busy) return
        busy = true
        clearMessages()
        AuthRepository.appScope.launch {
            try {
                block()
            } catch (e: AuthRepository.AuthException) {
                error = e.message
            } catch (e: Exception) {
                Log.e("SessionViewModel", "auth action failed", e)
                error = "Something went wrong: ${e.message ?: e.javaClass.simpleName}"
            } finally {
                busy = false
            }
        }
    }
}
