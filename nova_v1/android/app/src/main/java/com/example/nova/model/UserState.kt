package com.example.nova.model

import java.util.TimeZone

/**
 * Live, transient estimate of the user's current situation, attached to every event sent
 * to the backend. Frozen seam per DESIGN.md §5.2/§6 - wire shape is
 * {activity, location_ctx, calendar_ctx, dnd, screen, timestamp, confidence}; change only
 * by team agreement. Not itself persisted - durable facts derived from it are promoted to
 * Persona, interactions are logged to Memory (CONTEXT.md "User State").
 *
 * Fields below `confidence` are additive Phase 2 extensions layered on top of the frozen
 * seam - the original 7 fields are untouched.
 */
data class UserState(
    // Frozen seam (DESIGN.md §6)
    val activity: String? = null,
    val locationCtx: String? = null,
    val calendarCtx: String? = null,
    val dnd: Boolean = false,
    val screen: Boolean = false,
    val timestamp: Long = System.currentTimeMillis(),
    val confidence: Float = 0f,
    val utcOffsetMinutes: Int = TimeZone.getDefault().getOffset(System.currentTimeMillis()) / 60_000,
    // Phase 2 sensor-inference signals
    val motion: String? = null,
    val ambientLightLux: Float? = null,
    val proximityNear: Boolean? = null,
    val networkType: String? = null,
    val batteryLevelPercent: Int? = null,
    val batteryCharging: Boolean? = null,
    val wiredHeadsetConnected: Boolean? = null,
    val bluetoothAudioConnected: Boolean? = null,
    // No-permission signals
    val ringerMode: String? = null,
    val musicActive: Boolean? = null,
    val interruptionFilter: String? = null,
    val screenOrientation: String? = null,
    val powerSaveMode: Boolean? = null,
    val airplaneMode: Boolean? = null,
    // Permissioned signals
    val stepCountSinceBoot: Int? = null,
    val callState: String? = null,
    val foregroundApp: String? = null,
    // Rich calendar detail (additive - calendarCtx frozen-seam string unchanged above)
    val currentEvents: List<CalendarEventInfo> = emptyList(),
    val upcomingEvents: List<CalendarEventInfo> = emptyList(),
)
