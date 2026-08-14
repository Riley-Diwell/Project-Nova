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
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
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
    private const val BASE_URL = "http://127.0.0.1:8000"
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
        /**
         * [confirmation] mirrors EventOut.confirmation (schemas/event_out.py):
         * "yes_no" when this turn left a yes/no question dangling (so the UI
         * can offer quick-reply buttons), "open" for a dangling question that
         * isn't yes/no-shaped, null otherwise.
         */
        data class Final(
            val speech: String,
            val actions: List<CalendarAction>,
            /** edit_calendar_event Actions this turn - applied immediately via
             * [com.example.nova.state.CalendarWriter.updateEvent], no confirmation needed. */
            val editActions: List<EditCalendarAction>,
            /** delete_calendar_event Actions this turn - always confirm with the user before
             * calling [com.example.nova.state.CalendarWriter.deleteEvent] with these. */
            val deleteActions: List<DeleteCalendarAction>,
            /** Names the Episode this turn wrote, for [postOutcome]. Absent if Memory was unreachable. */
            val episodeId: String?,
            val confirmation: String?,
        ) : EventResult()
        data class NeedMore(
            val sessionId: String,
            val requestType: String,
            val fromIso: String,
            val toIso: String,
        ) : EventResult()
    }

    /**
     * An add_calendar_event Action from the backend's actions[] (schemas/event_out.py). Every entry
     * there has the same shape - {tool, input, trigger, ran} - and describes one tool call the Intent
     * Surface made; this picks out the ones this client knows how to carry out, and the caller
     * executes them on-device via [com.example.nova.state.CalendarWriter].
     */
    data class CalendarAction(
        val title: String,
        val startIso: String,
        val endIso: String,
        val description: String?,
        /** RFC 5545 RRULE built from the backend's structured recurrence input, e.g.
         * "FREQ=WEEKLY;INTERVAL=2;COUNT=5" - null for a one-off event. */
        val rrule: String?,
    )

    /**
     * A delete_calendar_event Action. [title] is only for showing the user what they're being
     * asked to confirm - the actual delete keys off [eventId] alone.
     */
    data class DeleteCalendarAction(
        val eventId: Long,
        val title: String,
    )

    /**
     * An edit_calendar_event Action. Every field but [eventId] is null unless the model actually
     * changed it - [com.example.nova.state.CalendarWriter.updateEvent] reads the event's current
     * values for anything left null rather than this client guessing at "unchanged".
     */
    data class EditCalendarAction(
        val eventId: Long,
        val title: String?,
        val startIso: String?,
        val endIso: String?,
        val description: String?,
        val rrule: String?,
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

    /**
     * One Function tool's controller gain (DESIGN.md Section 5.7), as GET/PUT /tools/gain
     * speak it (backend/app/schemas/tool_gain.py). [value] is what reinforcement has learned
     * and [userOverride] is what the user dialled in on [com.example.nova.ui.screens.GainScreen];
     * [effective] is whichever the backend's dispatcher will actually use. Named userOverride
     * rather than `override` to keep clear of the Kotlin modifier keyword.
     */
    data class ToolGain(
        val name: String,
        val description: String,
        val value: Float,
        val userOverride: Float?,
        val effective: Float,
    ) {
        val isOverridden: Boolean get() = userOverride != null
    }

    /** Every registered Function tool and its current gain, for the Gain tab's dials. */
    suspend fun getToolGains(): List<ToolGain> = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$BASE_URL/tools/gain")
            .get()
            .build()

        client.newCall(request).execute().use { response ->
            val array = JSONArray(response.requireBody())
            (0 until array.length()).map { array.getJSONObject(it).toToolGain() }
        }
    }

    /**
     * Sets one tool's user gain override, or clears it with a null [override] so the tool
     * reverts to its learned value. Returns that tool's gain as the backend now holds it, so
     * the caller can render what actually landed rather than what it hoped for.
     */
    suspend fun setToolGain(name: String, override: Float?): ToolGain = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            // JSONObject.put(String, null) removes the key; the backend needs an explicit
            // null to tell "clear the override" apart from "field omitted".
            put("override", override?.toDouble() ?: JSONObject.NULL)
        }

        val request = Request.Builder()
            .url("$BASE_URL/tools/gain/$name")
            .put(body.toString().toRequestBody(JSON_MEDIA_TYPE))
            .build()

        client.newCall(request).execute().use { response ->
            JSONObject(response.requireBody()).toToolGain()
        }
    }

    // --- turn outcome (DESIGN.md Sections 5.5/5.7) ---------------------------

    /**
     * Reports how a turn ended, so the backend can record it against the Episode and move the
     * gain of whatever the turn did. "accepted" means the spoken reply was allowed to finish;
     * "rejected" means the user pressed stop and talked over it.
     *
     * Deliberately fire-and-forget: this is feedback about a turn that is already over, and a
     * failure to deliver it must never surface to the user or block the next thing they say.
     */
    suspend fun postOutcome(episodeId: String, accepted: Boolean) = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("episode_id", episodeId)
            put("outcome", if (accepted) "accepted" else "rejected")
        }
        val request = Request.Builder()
            .url("$BASE_URL/event/outcome")
            .post(body.toString().toRequestBody(JSON_MEDIA_TYPE))
            .build()

        try {
            client.newCall(request).execute().close()
        } catch (e: IOException) {
            // Swallowed on purpose - see above.
        }
    }

    // --- Knowledge Map (DESIGN.md Section 5.6) -------------------------------

    /**
     * One belief in the Persona store, as a node of the Knowledge Map.
     * [kind] is "fact" or "category": category nodes are the ontology skeleton
     * (opinions -> likes -> food) and carry only a label. [source] tells a fact
     * the user stated from one NOVA derived from their behaviour, which is the
     * distinction the map exists to make visible.
     */
    data class GraphNode(
        val id: String,
        val label: String,
        val kind: String,
        val source: String? = null,
        val confidence: Float? = null,
        val category: List<String> = emptyList(),
        val support: Int? = null,
        val detail: String? = null,
    ) {
        val isFact: Boolean get() = kind == "fact"
        val isDerived: Boolean get() = source == "derived"
    }

    /** [kind] is "category" (the declared ontology) or "similar" (discovered by
     *  vector proximity, with [weight] as the cosine similarity). */
    data class GraphEdge(
        val source: String,
        val target: String,
        val kind: String,
        val weight: Float,
    ) {
        val isSimilarity: Boolean get() = kind == "similar"
    }

    data class KnowledgeGraph(
        val nodes: List<GraphNode> = emptyList(),
        val edges: List<GraphEdge> = emptyList(),
    ) {
        val facts: List<GraphNode> get() = nodes.filter { it.isFact }
    }

    /**
     * The whole Persona as a graph. [minSimilarity] controls how densely facts
     * are linked - the backend's default sits in the gap measured between
     * related and unrelated pairs (backend/app/persona/graph.py), and the slider
     * on the map lets the user trade recall for legibility.
     */
    suspend fun getKnowledgeGraph(minSimilarity: Float? = null): KnowledgeGraph =
        withContext(Dispatchers.IO) {
            val url = StringBuilder("$BASE_URL/persona/graph")
            if (minSimilarity != null) url.append("?min_similarity=$minSimilarity")

            val request = Request.Builder().url(url.toString()).get().build()
            client.newCall(request).execute().use { response ->
                val json = JSONObject(response.requireBody())
                KnowledgeGraph(
                    nodes = json.optJSONArray("nodes").mapObjects { it.toGraphNode() },
                    edges = json.optJSONArray("edges").mapObjects { it.toGraphEdge() },
                )
            }
        }

    /**
     * Runs consolidation: turns what the user has done repeatedly into durable beliefs. Returns
     * how many of each kind were written, for the confirmation the map shows afterwards.
     *
     * Slow by nature - it reads the whole Episode log and makes a Claude call - so callers should
     * show progress rather than assume this returns promptly.
     */
    suspend fun consolidate(): Pair<Int, Int> = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$BASE_URL/persona/consolidate")
            .post(JSONObject().toString().toRequestBody(JSON_MEDIA_TYPE))
            .build()

        client.newCall(request).execute().use { response ->
            val json = JSONObject(response.requireBody())
            Pair(
                json.optJSONArray("derived")?.length() ?: 0,
                json.optJSONArray("stated")?.length() ?: 0,
            )
        }
    }

    /** Correct a belief. Re-embeds server-side, so it becomes findable by what
     *  it now says. Passing null for a field leaves it unchanged. */
    suspend fun editFact(id: String, text: String?, category: List<String>?): Unit =
        withContext(Dispatchers.IO) {
            val body = JSONObject().apply {
                if (text != null) put("text", text)
                if (category != null) put("category", JSONArray(category))
            }
            val request = Request.Builder()
                .url("$BASE_URL/persona/$id")
                .patch(body.toString().toRequestBody(JSON_MEDIA_TYPE))
                .build()
            client.newCall(request).execute().use { it.requireBody() }
        }

    /** Forget a belief entirely (Privacy pillar / REQ1). */
    suspend fun deleteFact(id: String): Unit = withContext(Dispatchers.IO) {
        val request = Request.Builder().url("$BASE_URL/persona/$id").delete().build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw IOException("Backend returned ${response.code}")
            }
        }
    }

    private inline fun <T> JSONArray?.mapObjects(transform: (JSONObject) -> T): List<T> =
        if (this == null) emptyList()
        else (0 until length()).map { transform(getJSONObject(it)) }

    private fun JSONObject.toGraphNode(): GraphNode = GraphNode(
        id = getString("id"),
        label = optString("label"),
        kind = optString("kind", "fact"),
        source = if (isNull("source")) null else optString("source"),
        confidence = if (isNull("confidence")) null else optDouble("confidence").toFloat(),
        category = optJSONArray("category").let { arr ->
            if (arr == null) emptyList() else (0 until arr.length()).map { arr.getString(it) }
        },
        support = if (isNull("support")) null else optInt("support"),
        detail = if (isNull("detail")) null else optString("detail"),
    )

    private fun JSONObject.toGraphEdge(): GraphEdge = GraphEdge(
        source = getString("source"),
        target = getString("target"),
        kind = optString("kind", "similar"),
        weight = optDouble("weight", 1.0).toFloat(),
    )

    private fun Response.requireBody(): String {
        val text = body?.string().orEmpty()
        if (!isSuccessful) throw IOException("Backend returned $code: $text")
        return text
    }

    private fun JSONObject.toToolGain(): ToolGain = ToolGain(
        name = getString("name"),
        description = optString("description"),
        value = optDouble("value", 0.0).toFloat(),
        userOverride = if (isNull("override")) null else optDouble("override").toFloat(),
        effective = optDouble("effective", 0.0).toFloat(),
    )

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
                editActions = json.optJSONArray("actions")?.toEditCalendarActions().orEmpty(),
                deleteActions = json.optJSONArray("actions")?.toDeleteCalendarActions().orEmpty(),
                episodeId = json.optString("episode_id").takeIf { it.isNotBlank() },
                confirmation = json.optString("confirmation").takeIf {
                    json.has("confirmation") && !json.isNull("confirmation")
                },
            )
        }
    }

    /**
     * Picks the executable Actions out of actions[]. Each entry is {tool, input, trigger, ran};
     * anything this client does not recognise is another tool's Action passing through, and
     * anything with ran=false was refused by that tool's gain and must NOT be carried out - it is
     * there so the turn's record is complete, not as an instruction.
     */
    private fun JSONArray.toCalendarActions(): List<CalendarAction> =
        (0 until length()).mapNotNull { i ->
            val obj = optJSONObject(i) ?: return@mapNotNull null
            if (obj.optString("tool") != "add_calendar_event") return@mapNotNull null
            if (!obj.optBoolean("ran", false)) return@mapNotNull null
            val input = obj.optJSONObject("input") ?: return@mapNotNull null
            val title = input.optString("title")
            val start = input.optString("start_time")
            val end = input.optString("end_time")
            if (title.isBlank() || start.isBlank() || end.isBlank()) return@mapNotNull null
            val recurrence = input.optJSONObject("recurrence")
                .takeIf { input.has("recurrence") && !input.isNull("recurrence") }
            CalendarAction(
                title = title,
                startIso = start,
                endIso = end,
                description = input.optString("description")
                    .takeIf { input.has("description") && !input.isNull("description") },
                rrule = recurrence?.let { buildRrule(it) },
            )
        }

    /**
     * edit_calendar_event Actions, picked out the same way as add's - only ran=true ones, since
     * ran=false was refused by that tool's gain and must not be carried out. Every field but
     * event_id is optional on the wire (only changed fields are present), so each one is only
     * populated when the backend actually sent it.
     */
    private fun JSONArray.toEditCalendarActions(): List<EditCalendarAction> =
        (0 until length()).mapNotNull { i ->
            val obj = optJSONObject(i) ?: return@mapNotNull null
            if (obj.optString("tool") != "edit_calendar_event") return@mapNotNull null
            if (!obj.optBoolean("ran", false)) return@mapNotNull null
            val input = obj.optJSONObject("input") ?: return@mapNotNull null
            if (!input.has("event_id") || input.isNull("event_id")) return@mapNotNull null
            val recurrence = input.optJSONObject("recurrence")
                .takeIf { input.has("recurrence") && !input.isNull("recurrence") }
            EditCalendarAction(
                eventId = input.optLong("event_id"),
                title = input.optString("title")
                    .takeIf { input.has("title") && !input.isNull("title") },
                startIso = input.optString("start_time")
                    .takeIf { input.has("start_time") && !input.isNull("start_time") },
                endIso = input.optString("end_time")
                    .takeIf { input.has("end_time") && !input.isNull("end_time") },
                description = input.optString("description")
                    .takeIf { input.has("description") && !input.isNull("description") },
                rrule = recurrence?.let { buildRrule(it) },
            )
        }

    /**
     * delete_calendar_event Actions, picked out the same way as add's - only ran=true ones, since
     * ran=false was refused by that tool's gain and must not be carried out.
     */
    private fun JSONArray.toDeleteCalendarActions(): List<DeleteCalendarAction> =
        (0 until length()).mapNotNull { i ->
            val obj = optJSONObject(i) ?: return@mapNotNull null
            if (obj.optString("tool") != "delete_calendar_event") return@mapNotNull null
            if (!obj.optBoolean("ran", false)) return@mapNotNull null
            val input = obj.optJSONObject("input") ?: return@mapNotNull null
            if (!input.has("event_id") || input.isNull("event_id")) return@mapNotNull null
            val title = input.optString("title")
            if (title.isBlank()) return@mapNotNull null
            DeleteCalendarAction(eventId = input.optLong("event_id"), title = title)
        }

    /**
     * Builds an RFC 5545 RRULE from add_calendar_event's structured recurrence input
     * (frequency/interval/count/until - see calendar_tool.py). [until] is the user's local wall
     * clock like every other calendar time on this wire, so it's converted to the UTC form RRULE
     * requires when DTSTART carries a time zone (which CalendarWriter always sets).
     */
    private fun buildRrule(recurrence: JSONObject): String? {
        val frequency = recurrence.optString("frequency").uppercase().takeIf { it.isNotBlank() }
            ?: return null
        val parts = mutableListOf("FREQ=$frequency")

        val interval = recurrence.optInt("interval", 1)
        if (interval > 1) parts += "INTERVAL=$interval"

        if (recurrence.has("count") && !recurrence.isNull("count")) {
            parts += "COUNT=${recurrence.optInt("count")}"
        } else if (recurrence.has("until") && !recurrence.isNull("until")) {
            val until = recurrence.optString("until")
            try {
                val utc = LocalDateTime.parse(until)
                    .atZone(ZoneId.systemDefault())
                    .withZoneSameInstant(ZoneId.of("UTC"))
                parts += "UNTIL=${utc.format(DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmss'Z'"))}"
            } catch (e: DateTimeParseException) {
                // Skip the bound rather than the whole recurrence - LLM-produced input, not a
                // validated wire contract (same stance as writeCalendarActions in VoiceScreen.kt).
            }
        }

        return parts.joinToString(";")
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
        put("start_local", startLocal)
        put("end_local", endLocal)
        put("location", location)
        put("availability", availability)
        put("is_all_day", isAllDay)
        put("self_status", selfStatus)
        put("minutes_until_start", minutesUntilStart)
        put("event_id", eventId)
    }
}
