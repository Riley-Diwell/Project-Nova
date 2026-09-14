package com.example.novav2.state

import android.content.Context
import com.example.novav2.model.UserState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * Latest [UserState] snapshot, kept fresh by [com.example.novav2.service.SignalMonitorService]
 * every 10s regardless of whether any screen is on-screen to observe it.
 */
object SignalRepository {
    private val _userState = MutableStateFlow<UserState?>(null)
    val userState: StateFlow<UserState?> = _userState

    fun update(context: Context) {
        _userState.value = UserStateCollector.snapshot(context)
    }
}
