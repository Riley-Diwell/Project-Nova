package com.example.nova.network

import com.example.nova.model.CalendarEventInfo
import com.example.nova.model.UserState
import org.json.JSONArray
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject
import java.io.IOException
import java.time.Instant
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * Talks to the backend's POST /event seam (DESIGN.md Sections 5.1/5.3/6:
 * {event, user_state} -> {speech, actions[]}). [BASE_URL] targets the dev machine's LAN IP
 * for testing on a physical device on the same WiFi network - run `uvicorn main:app --reload
 * --host 0.0.0.0` on the backend (not just the bare default, which only binds to loopback),
 * and update this IP if it changes (e.g. reconnecting to a different network). Use
 * "http://10.0.2.2:8000" instead if running against the Android emulator.
 */
object NovaApiClient {
    private const val BASE_URL = "http://172.20.10.4:8000"
    private val JSON_MEDIA_TYPE = "application/json".toMediaType()

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build()

    /**
     * Mirrors the backend's EventOut/NeedMoreOut union (schemas/event_out.py). [Final] is a
     * completed turn ready to speak; [NeedMore] means the Intent Surface paused mid-conversation
     * on a client-executed tool (e.g. get_calendar_range) and is waiting on [postContinueEvent]
     * with the on-device result, keyed by [NeedMore.sessionId].
     */
    sealed class EventResult {
        data class Final(val speech: String, val actions: List<CalendarAction>) : EventResult()
        data class NeedMore(
            val sessionId: String,
            val requestType: String,
            val fromIso: String,
            val toIso: String,
        ) : EventResult()
    }

    /**
     * Mirrors a "calendar.create_event" entry from the backend's actions[] (schemas/event_out.py) -
     * the Intent Surface queues these when add_calendar_event is called; the caller is expected to
     * execute them on-device via [com.example.nova.state.CalendarWriter].
     */
    data class CalendarAction(
        val title: String,
        val startIso: String,
        val endIso: String,
        val description: String?,
    )

    /** Posts a voice transcript + [UserState] snapshot to /event and returns the spoken reply. */
    suspend fun postVoiceEvent(transcript: String, userState: UserState): EventResult =
        withContext(Dispatchers.IO) {
            val body = JSONObject().apply {
                put("event", JSONObject().apply {
                    put("id", UUID.randomUUID().toString())
                    put("timestamp", Instant.now().toString())
                    put("type", "voice")
                    put("text", transcript)
                })
                put("user_state", userState.toJson())
            }

            val request = Request.Builder()
                .url("$BASE_URL/event")
                .post(body.toString().toRequestBody(JSON_MEDIA_TYPE))
                .build()

            client.newCall(request).execute().use { parseEventResponse(it) }
        }

    /**
     * Resumes a paused conversation after resolving a [EventResult.NeedMore] request on-device
     * (e.g. querying [com.example.nova.state.CalendarSignal.rangeSnapshot] for the requested
     * range). May itself return another [EventResult.NeedMore] if the model needs a further hop.
     */
    suspend fun postContinueEvent(sessionId: String, events: List<CalendarEventInfo>): EventResult =
        withContext(Dispatchers.IO) {
            val body = JSONObject().apply {
                put("session_id", sessionId)
                put("result", JSONObject().apply {
                    put("events", events.toJsonArray())
                })
            }

            val request = Request.Builder()
                .url("$BASE_URL/event/continue")
                .post(body.toString().toRequestBody(JSON_MEDIA_TYPE))
                .build()

            client.newCall(request).execute().use { parseEventResponse(it) }
        }

    private fun parseEventResponse(response: Response): EventResult {
        val responseBody = response.body?.string().orEmpty()
        if (!response.isSuccessful) {
            throw IOException("Backend returned ${response.code}: $responseBody")
        }
        val json = JSONObject(responseBody)
        return when (json.optString("status", "final")) {
            "need_more" -> {
                val req = json.optJSONObject("request") ?: JSONObject()
                EventResult.NeedMore(
                    sessionId = json.getString("session_id"),
                    requestType = req.optString("type"),
                    fromIso = req.optString("from"),
                    toIso = req.optString("to"),
                )
            }
            else -> EventResult.Final(
                speech = json.optString("speech", "..."),
                actions = json.optJSONArray("actions")?.toCalendarActions().orEmpty(),
            )
        }
    }

    private fun JSONArray.toCalendarActions(): List<CalendarAction> =
        (0 until length()).mapNotNull { i ->
            val obj = optJSONObject(i) ?: return@mapNotNull null
            if (obj.optString("type") != "calendar.create_event") return@mapNotNull null
            val title = obj.optString("title")
            val start = obj.optString("start_time")
            val end = obj.optString("end_time")
            if (title.isBlank() || start.isBlank() || end.isBlank()) return@mapNotNull null
            CalendarAction(
                title = title,
                startIso = start,
                endIso = end,
                description = obj.optString("description").takeIf { obj.has("description") && !obj.isNull("description") },
            )
        }

    /** Wire shape per DESIGN.md Section 5.2 - snake_case keys to match the backend Pydantic schema. */
    private fun UserState.toJson(): JSONObject = JSONObject().apply {
        put("activity", activity)
        put("location_ctx", locationCtx)
        put("calendar_ctx", calendarCtx)
        put("dnd", dnd)
        put("screen", screen)
        put("timestamp", timestamp)
        put("confidence", confidence)
        put("utc_offset_minutes", utcOffsetMinutes)
        put("motion", motion)
        put("ambient_light_lux", ambientLightLux)
        put("proximity_near", proximityNear)
        put("network_type", networkType)
        put("battery_level_percent", batteryLevelPercent)
        put("battery_charging", batteryCharging)
        put("wired_headset_connected", wiredHeadsetConnected)
        put("bluetooth_audio_connected", bluetoothAudioConnected)
        put("ringer_mode", ringerMode)
        put("music_active", musicActive)
        put("interruption_filter", interruptionFilter)
        put("screen_orientation", screenOrientation)
        put("power_save_mode", powerSaveMode)
        put("airplane_mode", airplaneMode)
        put("step_count_since_boot", stepCountSinceBoot)
        put("call_state", callState)
        put("foreground_app", foregroundApp)
        put("current_events", currentEvents.toJsonArray())
        put("upcoming_events", upcomingEvents.toJsonArray())
    }

    private fun List<CalendarEventInfo>.toJsonArray(): JSONArray =
        JSONArray().also { arr -> forEach { arr.put(it.toJson()) } }

    private fun CalendarEventInfo.toJson(): JSONObject = JSONObject().apply {
        put("title", title)
        put("start_millis", startMillis)
        put("end_millis", endMillis)
        put("location", location)
        put("availability", availability)
        put("is_all_day", isAllDay)
        put("self_status", selfStatus)
        put("minutes_until_start", minutesUntilStart)
    }
}
