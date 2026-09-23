package com.example.novav2.state

import androidx.lifecycle.Lifecycle
import androidx.lifecycle.ProcessLifecycleOwner

/**
 * Whether Nova's own UI is already the thing on screen. Backed by ProcessLifecycleOwner rather
 * than an Activity-specific flag so it reflects "some Nova screen is visible", not just
 * MainActivity by name. Main-thread only - every current caller (AssistVoiceService's
 * finishTurn/onCreate, AssistTrampolineActivity's onCreate) already runs on the main thread.
 */
object AppForegroundState {
    fun isInForeground(): Boolean =
        ProcessLifecycleOwner.get().lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)
}
