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

    // How often SignalMonitorService posts an ambient Event (see its own doc comment). Lives
    // here, not there, because this is the one fact both the service (schedules against it) and
    // StateScreen's debug countdown (renders against it) need to agree on.
    const val AMBIENT_CHECK_INTERVAL_MILLIS = 10 * 60 * 1000L

    private val _nextAmbientCheckAtMillis = MutableStateFlow<Long?>(null)
    /** Epoch millis of the next ambient check - debug-only, for StateScreen's countdown circle.
     * Null until SignalMonitorService has run at least once. */
    val nextAmbientCheckAtMillis: StateFlow<Long?> = _nextAmbientCheckAtMillis

    private val _lastAmbientCheck = MutableStateFlow<AmbientCheckOutcome?>(null)
    /** What [AmbientCheckRunner] found last time it actually ran - debug-only, for StateScreen's
     * "last check" readout. Null until one has happened (an on-device-gated tick that declined
     * to even ask doesn't count - see SignalMonitorService.maybePostAmbientEvent). */
    val lastAmbientCheck: StateFlow<AmbientCheckOutcome?> = _lastAmbientCheck

    fun update(context: Context) {
        _userState.value = UserStateCollector.snapshot(context)
    }

    fun scheduleNextAmbientCheck(atMillis: Long) {
        _nextAmbientCheckAtMillis.value = atMillis
    }

    fun recordAmbientCheck(outcome: AmbientCheckOutcome) {
        _lastAmbientCheck.value = outcome
    }
}

enum class AmbientCheckResult { SPOKE, QUIET, BLOCKED, FAILED }

data class AmbientCheckOutcome(val atMillis: Long, val result: AmbientCheckResult)
