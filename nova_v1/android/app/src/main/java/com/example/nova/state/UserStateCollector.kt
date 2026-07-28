package com.example.nova.state

import android.content.Context
import com.example.nova.model.UserState

/**
 * Fuses all signals into a [UserState] snapshot (DESIGN.md §5.2).
 *
 * Confidence = known signals / 17 total possible:
 *   - 9 always readable (dnd, screen, network, battery, ringerMode, musicActive, interruptionFilter,
 *     powerSaveMode, airplaneMode)
 *   - 10 optional (permission-gated or sensor-dependent):
 *     calendarCtx, activity, locationCtx, motion, ambientLightLux, proximityNear,
 *     screenOrientation, stepCountSinceBoot, callState, foregroundApp
 */
object UserStateCollector {
    fun snapshot(context: Context): UserState {
        val calendarSnap = CalendarSignal.snapshot(context)
        val calendarCtx = calendarSnap?.calendarCtx
        val activity = ActivitySignal.latestActivity
        val locationCtx = LocationSignal.latestLocationCtx
        val motion = SensorSignal.motion
        val ambientLightLux = SensorSignal.ambientLightLux
        val proximityNear = SensorSignal.proximityNear
        val screenOrientation = SensorSignal.screenOrientation
        val stepCountSinceBoot = SensorSignal.stepCountSinceBoot
        val battery = BatterySignal.currentBatteryState(context)
        val audioRoute = AudioRouteSignal.currentAudioRoute(context)
        val callState = CallStateSignal.currentCallState(context)
        val foregroundApp = ForegroundAppSignal.currentForegroundApp(context)

        val optionalKnown = listOfNotNull<Any>(
            calendarCtx, activity, locationCtx, motion, ambientLightLux, proximityNear,
            screenOrientation, stepCountSinceBoot, callState, foregroundApp
        ).size

        return UserState(
            activity = activity,
            locationCtx = locationCtx,
            calendarCtx = calendarCtx,
            dnd = DndSignal.isDndActive(context),
            screen = ScreenSignal.isScreenOn(context),
            confidence = (9 + optionalKnown) / 19f,
            motion = motion,
            ambientLightLux = ambientLightLux,
            proximityNear = proximityNear,
            networkType = NetworkSignal.currentNetworkType(context),
            batteryLevelPercent = battery.levelPercent,
            batteryCharging = battery.charging,
            wiredHeadsetConnected = audioRoute.wiredHeadsetConnected,
            bluetoothAudioConnected = audioRoute.bluetoothAudioConnected,
            ringerMode = RingerModeSignal.currentRingerMode(context),
            musicActive = AudioActiveSignal.isMusicActive(context),
            interruptionFilter = InterruptionFilterSignal.currentFilter(context),
            powerSaveMode = PowerSaveModeSignal.isPowerSaveMode(context),
            airplaneMode = AirplaneModeSignal.isAirplaneModeOn(context),
            screenOrientation = screenOrientation,
            stepCountSinceBoot = stepCountSinceBoot,
            callState = callState,
            foregroundApp = foregroundApp,
            currentEvents = calendarSnap?.currentEvents ?: emptyList(),
            upcomingEvents = calendarSnap?.upcomingEvents ?: emptyList(),
        )
    }
}
