package com.example.novav2.network

import com.example.novav2.BuildConfig
import com.example.novav2.model.CalendarEventInfo
import com.example.novav2.model.UserState
import org.json.JSONArray
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
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
 * {event, user_state} -> {speech, actions[]}). [BASE_URL] points at the deployed
 * `nova-v2` Cloud Run service (nova_v2/server, region australia-southeast1) -
 * swap back to "http://10.0.2.2:8000" (emulator host-loopback) or a LAN IP for
 * local dev against `uvicorn --reload` instead. [API_KEY] must match that
 * service's NOVA_API_KEY secret (main.py's _require_api_key) - set via
 * local.properties' NOVA_API_KEY (gitignored, machine-local; blank there means
 * no header is sent, fine only for a backend with no key configured, i.e. bare
 * local dev).
 */
object NovaApiClient {
    private const val BASE_URL = "https://nova-v2-1021689546881.australia-southeast1.run.app"
    private val API_KEY = BuildConfig.NOVA_API_KEY
    private val JSON_MEDIA_TYPE = "application/json".toMediaType()

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .addInterceptor { chain ->
            val request = if (API_KEY.isEmpty()) chain.request()
                else chain.request().newBuilder().addHeader("X-Nova-Api-Key", API_KEY).build()
            chain.proceed(request)
        }
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
             * [com.example.novav2.state.CalendarWriter.updateEvent], no confirmation needed. */
            val editActions: List<EditCalendarAction>,
            /** delete_calendar_event Actions this turn - always confirm with the user before
             * calling [com.example.novav2.state.CalendarWriter.deleteEvent] with these. */
            val deleteActions: List<DeleteCalendarAction>,
            /** set_timer Actions this turn - fired immediately via
             * [com.example.novav2.state.AlarmIntents.setTimer], no confirmation needed. */
            val timerActions: List<TimerAction>,
            /** set_alarm Actions this turn - fired immediately via
             * [com.example.novav2.state.AlarmIntents.setAlarm], no confirmation needed. */
            val alarmActions: List<AlarmAction>,
            /** Names the Episode this turn wrote, for [postOutcome]. Absent if Memory was unreachable. */
            val episodeId: String?,
            val confirmation: String?,
            /** Set when navigation_departure_time ran this turn and could measure a countdown -
             * present even when [speech] is empty (an ambient check that isn't urgent yet, but now
             * knows exactly when it will be). See [com.example.novav2.state.DepartureAlarmScheduler]. */
            val scheduledDeparture: ScheduledDeparture?,
        ) : EventResult()
        data class NeedMore(
            val sessionId: String,
            val requestType: String,
            val fromIso: String,
            val toIso: String,
        ) : EventResult()
    }

    /** Mirrors EventOut.scheduled_departure - {destination, mode, leave_in_minutes,
     * minutes_until_start, event_title}. */
    data class ScheduledDeparture(
        val destination: String?,
        val mode: String?,
        val leaveInMinutes: Double,
        /** The commitment's own countdown - null for a destination with no calendar anchor
         * (see intent_surface.py's WHEN TO LEAVE). [AmbientCheckRunner.kt]'s notification text
         * needs both this and [leaveInMinutes], since a bare "leave in N minutes" doesn't say
         * what for. */
        val minutesUntilStart: Double?,
        /** That calendar entry's own title, copied verbatim server-side - never model-phrased.
         * Same null condition as [minutesUntilStart]. Falls back to [destination] in the
         * notification text when absent (see AmbientCheckRunner.kt's leaveSoonText). */
        val eventTitle: String?,
    )

    /**
     * An add_calendar_event Action from the backend's actions[] (schemas/event_out.py). Every entry
     * there has the same shape - {tool, input, trigger, ran} - and describes one tool call the Intent
     * Surface made; this picks out the ones this client knows how to carry out, and the caller
     * executes them on-device via [com.example.novav2.state.CalendarWriter].
     */
    data class CalendarAction(
        val title: String,
        val startIso: String,
        val endIso: String,
        val description: String?,
        val location: String?,
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
     * changed it - [com.example.novav2.state.CalendarWriter.updateEvent] reads the event's current
     * values for anything left null rather than this client guessing at "unchanged".
     */
    data class EditCalendarAction(
        val eventId: Long,
        val title: String?,
        val startIso: String?,
        val endIso: String?,
        val description: String?,
        val location: String?,
        val rrule: String?,
    )

    /** A set_timer Action - fires [com.example.novav2.state.AlarmIntents.setTimer] the moment it
     * arrives, same fire-and-forget shape as [CalendarAction]. */
    data class TimerAction(
        val durationSeconds: Int,
        val label: String?,
    )

    /** A set_alarm Action - fires [com.example.novav2.state.AlarmIntents.setAlarm] the moment it
     * arrives. [hour]/[minute] are the user's local wall clock, as resolved server-side. */
    data class AlarmAction(
        val hour: Int,
        val minute: Int,
        val label: String?,
    )

    /** Posts a voice transcript + [UserState] snapshot to /event and returns the spoken reply. */
    suspend fun postVoiceEvent(transcript: String, userState: UserState): EventResult =
        postEvent(userState) {
            put("type", "voice")
            put("text", transcript)
        }

    /**
     * Posts a bare "timestamp" Event (schemas/event.py's TimeEvent) - no user speech, just this
     * turn's [UserState] snapshot. This is the ambient heartbeat [com.example.novav2.service.
     * SignalMonitorService] posts periodically so navigation_departure_time (and anything else
     * with enough gain) gets a chance to act on inferred state rather than only ever running
     * inside a voice turn. A [EventResult.NeedMore] response (e.g. the model wanting a client-tool
     * round-trip) is valid but unhandled by ambient callers today - there is no open conversation
     * to resume it on, so it's dropped rather than answered.
     */
    suspend fun postAmbientEvent(userState: UserState): EventResult =
        postEvent(userState) {
            put("type", "timestamp")
        }

    /**
     * The envelope every /event POST shares - an id, a timestamp and [userState] - with
     * [eventFields] filling in whatever the specific event type still needs (just `type` for an
     * ambient timestamp; `type` and `text` for a voice transcript).
     */
    private suspend fun postEvent(
        userState: UserState,
        eventFields: JSONObject.() -> Unit,
    ): EventResult = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("event", JSONObject().apply {
                put("id", UUID.randomUUID().toString())
                put("timestamp", Instant.now().toString())
                eventFields()
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
     * (e.g. querying [com.example.novav2.state.CalendarSignal.rangeSnapshot] for the requested
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
     * and [userOverride] is what the user dialled in on [com.example.novav2.ui.screens.GainScreen];
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
    )

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

    // --- Audit log (Autonomy pillar) -----------------------------------------

    /**
     * One Tool call as the audit log shows it (schemas/audit.py's AuditEntryOut) - one row
     * per entry in a past turn's actions[], newest turn first. [reason] is the Controller's
     * Decision.reason (e.g. "zero_gain") explaining why the tool did or didn't run; it is
     * shown here even though the model itself is never allowed to see it back
     * (intent_surface.py's _redact_control_trace). [context] and [summary] are plain-English,
     * computed server-side (tools/core/narration.py) - render these rather than [tool]/[input].
     */
    data class AuditEntry(
        val episodeId: String,
        val occurredAt: String?,
        val eventType: String,
        val context: String?,
        val tool: String,
        val trigger: String,
        val ran: Boolean,
        val reason: String?,
        val speech: String?,
        val summary: String,
    )

    /**
     * Automated actions Nova has taken, newest first, for the Audit tab. [since]/[until] are
     * ISO instants bounding the search (either or both may be omitted for an open range);
     * [tool] restricts to one Function tool's calls; [q] free-text searches summary/context/
     * speech/reason/tool server-side (narration.py's matches_query).
     */
    suspend fun getAuditLog(
        limit: Int = 50,
        since: String? = null,
        until: String? = null,
        tool: String? = null,
        q: String? = null,
    ): List<AuditEntry> = withContext(Dispatchers.IO) {
        val url = "$BASE_URL/audit".toHttpUrl().newBuilder()
            .addQueryParameter("limit", limit.toString())
            .apply {
                since?.let { addQueryParameter("since", it) }
                until?.let { addQueryParameter("until", it) }
                tool?.let { addQueryParameter("tool", it) }
                q?.takeIf { it.isNotBlank() }?.let { addQueryParameter("q", it) }
            }
            .build()
        val request = Request.Builder().url(url).get().build()

        client.newCall(request).execute().use { response ->
            val array = JSONArray(response.requireBody())
            (0 until array.length()).map { array.getJSONObject(it).toAuditEntry() }
        }
    }

    private fun JSONObject.toAuditEntry(): AuditEntry = AuditEntry(
        episodeId = getString("episode_id"),
        occurredAt = if (isNull("occurred_at")) null else optString("occurred_at"),
        eventType = optString("event_type"),
        context = if (isNull("context")) null else optString("context"),
        tool = getString("tool"),
        trigger = optString("trigger", "requested"),
        ran = optBoolean("ran", false),
        reason = if (isNull("reason")) null else optString("reason"),
        speech = if (isNull("speech")) null else optString("speech"),
        summary = optString("summary"),
    )

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
                timerActions = json.optJSONArray("actions")?.toTimerActions().orEmpty(),
                alarmActions = json.optJSONArray("actions")?.toAlarmActions().orEmpty(),
                episodeId = json.optString("episode_id").takeIf { it.isNotBlank() },
                confirmation = json.optString("confirmation").takeIf {
                    json.has("confirmation") && !json.isNull("confirmation")
                },
                scheduledDeparture = json.optJSONObject("scheduled_departure")?.let {
                    ScheduledDeparture(
                        destination = it.optString("destination").takeIf { d -> d.isNotBlank() },
                        mode = it.optString("mode").takeIf { m -> m.isNotBlank() },
                        leaveInMinutes = it.optDouble("leave_in_minutes"),
                        minutesUntilStart = it.optDouble("minutes_until_start")
                            .takeIf { v -> !v.isNaN() },
                        eventTitle = it.optString("event_title").takeIf { t -> t.isNotBlank() },
                    )
                },
            )
        }
    }

    /**
     * The {tool, input, trigger, ran} entries in actions[] belonging to [tool] that actually
     * ran - ran=false was refused by that tool's gain and must NOT be carried out, it is there
     * so the turn's record is complete, not as an instruction. Shared preamble behind every
     * toXActions() below; each still does its own field extraction from here, since which
     * fields are required and how they're validated genuinely differs per tool.
     */
    private fun JSONArray.inputsFor(tool: String): List<JSONObject> =
        (0 until length()).mapNotNull { i ->
            val obj = optJSONObject(i) ?: return@mapNotNull null
            if (obj.optString("tool") != tool) return@mapNotNull null
            if (!obj.optBoolean("ran", false)) return@mapNotNull null
            obj.optJSONObject("input")
        }

    private fun JSONArray.toCalendarActions(): List<CalendarAction> =
        inputsFor("add_calendar_event").mapNotNull { input ->
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
                location = input.optString("location")
                    .takeIf { input.has("location") && !input.isNull("location") },
                rrule = recurrence?.let { buildRrule(it) },
            )
        }

    /**
     * Every field but event_id is optional on the wire (only changed fields are present), so
     * each one is only populated when the backend actually sent it.
     */
    private fun JSONArray.toEditCalendarActions(): List<EditCalendarAction> =
        inputsFor("edit_calendar_event").mapNotNull { input ->
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
                location = input.optString("location")
                    .takeIf { input.has("location") && !input.isNull("location") },
                rrule = recurrence?.let { buildRrule(it) },
            )
        }

    private fun JSONArray.toDeleteCalendarActions(): List<DeleteCalendarAction> =
        inputsFor("delete_calendar_event").mapNotNull { input ->
            if (!input.has("event_id") || input.isNull("event_id")) return@mapNotNull null
            val title = input.optString("title")
            if (title.isBlank()) return@mapNotNull null
            DeleteCalendarAction(eventId = input.optLong("event_id"), title = title)
        }

    private fun JSONArray.toTimerActions(): List<TimerAction> =
        inputsFor("set_timer").mapNotNull { input ->
            if (!input.has("duration_seconds") || input.isNull("duration_seconds")) return@mapNotNull null
            TimerAction(
                durationSeconds = input.optInt("duration_seconds"),
                label = input.optString("label").takeIf { input.has("label") && !input.isNull("label") },
            )
        }

    private fun JSONArray.toAlarmActions(): List<AlarmAction> =
        inputsFor("set_alarm").mapNotNull { input ->
            if (!input.has("hour") || input.isNull("hour")) return@mapNotNull null
            if (!input.has("minute") || input.isNull("minute")) return@mapNotNull null
            AlarmAction(
                hour = input.optInt("hour"),
                minute = input.optInt("minute"),
                label = input.optString("label").takeIf { input.has("label") && !input.isNull("label") },
            )
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
        put("preferred_travel_mode", preferredTravelMode)
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
