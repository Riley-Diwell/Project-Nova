package com.example.novav2.network

import com.example.novav2.BuildConfig

import com.example.novav2.knowledge.KnowledgeRepository
import com.example.novav2.model.CalendarEventInfo
import com.example.novav2.model.ReminderSummary
import com.example.novav2.model.UserState
import org.json.JSONArray
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import com.example.novav2.network.NovaHttp.withNovaAuth
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
 * local dev against `uvicorn --reload` instead. The client key ([NovaHttp]) must match that
 * service's NOVA_API_KEY secret (main.py's _require_api_key) - set via
 * local.properties' NOVA_API_KEY (gitignored, machine-local; blank there means
 * no header is sent, fine only for a backend with no key configured, i.e. bare
 * local dev).
 */
object NovaApiClient {
    /**
     * The backend, from NOVA_BASE_URL in local.properties (gitignored - this repo is public and
     * the server is a private tailnet machine). Self-hosted, see nova_v2/deploy: reachable only
     * over the tailnet, so the phone needs the Tailscale app signed in. `tailscale serve` gives
     * it a real certificate, so this is ordinary HTTPS.
     *
     * Not private: NotesApiClient and friends talk to the same server.
     */
    const val BASE_URL = BuildConfig.NOVA_BASE_URL
    private val JSON_MEDIA_TYPE = "application/json".toMediaType()

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .withNovaAuth()
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
            /** set_reminder Actions this turn - stored and scheduled via
             * [com.example.novav2.state.TurnActionApplier], no confirmation needed. */
            val reminderActions: List<ReminderAction> = emptyList(),
            /** update_reminder Actions this turn (complete / snooze / edit / delete) - applied
             * immediately; a delete is a soft delete with an Undo notice. */
            val updateReminderActions: List<UpdateReminderAction> = emptyList(),
            /** memory-tool recalls this turn - the Voice tab shows the notes they found as
             * chips (see NoteRecallActions.kt). */
            val recallActions: List<RecallAction> = emptyList(),
        ) : EventResult()
        data class NeedMore(
            val sessionId: String,
            val requestType: String,
            val fromIso: String,
            val toIso: String,
            /** get_reminders only - whether to include completed reminders. */
            val includeDone: Boolean = false,
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
        postContinueResult(sessionId, JSONObject().apply { put("events", events.toJsonArray()) })

    /** The get_reminders side of [postContinueEvent]: {"reminders": [...]} in the same
     * ReminderInfo shape the voice window uses. */
    suspend fun postContinueReminders(sessionId: String, reminders: List<ReminderSummary>): EventResult =
        postContinueResult(sessionId, JSONObject().apply { put("reminders", reminders.toReminderJsonArray()) })

    /** /event/continue with whatever [result] shape the paused client tool calls for - the
     * backend feeds it straight back to the model as that tool's result. */
    suspend fun postContinueResult(sessionId: String, result: JSONObject): EventResult =
        withContext(Dispatchers.IO) {
            val body = JSONObject().apply {
                put("session_id", sessionId)
                put("result", result)
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
     * [kind] is "fact", "category" or "cluster":
     *  - category nodes are the ontology skeleton (opinions -> likes -> food), sent by servers
     *    that don't group by meaning yet;
     *  - cluster nodes are the server's meaning groups - [label] is the group's heading, [size]
     *    how many facts it holds - and each fact names its group in [clusterId].
     * [source] tells a fact the user stated from one NOVA derived from their behaviour, which is
     * the distinction the map exists to make visible. Fields a server doesn't send are null.
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
        /** The note this belief was promoted from, if any. */
        val noteId: String? = null,
        /** The meaning group this fact belongs to (a "cluster:<id>" node's id). */
        val clusterId: String? = null,
        /** A cluster node's member count. */
        val size: Int? = null,
        /** When the belief was last said or seen, ISO 8601. */
        val statedAt: String? = null,
    ) {
        val isFact: Boolean get() = kind == "fact"
        val isCluster: Boolean get() = kind == "cluster"
        val isDerived: Boolean get() = source == "derived"
    }

    /** [kind] is "category" (the declared ontology), "similar" (fact to fact, discovered by
     *  vector proximity) or "topic" (a fact to a topic it is also about, beyond its own - target
     *  is the topic's node id). [weight] is the cosine similarity for the last two. */
    data class GraphEdge(
        val source: String,
        val target: String,
        val kind: String,
        val weight: Float,
    ) {
        val isSimilarity: Boolean get() = kind == "similar"
        val isTopicLink: Boolean get() = kind == "topic"
    }

    data class KnowledgeGraph(
        val nodes: List<GraphNode> = emptyList(),
        val edges: List<GraphEdge> = emptyList(),
    )

    /** A graph and the server's ETag for it, to send back as If-None-Match next time. */
    data class TaggedGraph(val graph: KnowledgeGraph, val etag: String?)

    /**
     * What the server says about background learning, from GET /persona/graph's headers:
     * [status] is "due" (the phone should start a run), "running" or "idle"; [lastRunAt] is ISO.
     * Servers that don't consolidate automatically send neither, and the hint is null.
     */
    data class ConsolidationHint(val status: String, val lastRunAt: String?)

    /** A graph fetch: [tagged] is null when the held graph is still current (a 304). */
    data class GraphFetch(val tagged: TaggedGraph?, val hint: ConsolidationHint?)

    /**
     * The whole Persona as a graph. [minSimilarity] controls how densely facts
     * are linked - the backend's default sits in the gap measured between
     * related and unrelated pairs (server/app/store/persona/graph.py). [clusters] asks for meaning
     * groups instead of the category skeleton; servers without them ignore it.
     *
     * Pass the [TaggedGraph.etag] of the graph already held as [ifNoneMatch]: [GraphFetch.tagged]
     * comes back null (a 304, no body) when it is still current.
     */
    suspend fun getKnowledgeGraph(
        minSimilarity: Float? = null,
        ifNoneMatch: String? = null,
        clusters: Boolean = true,
    ): GraphFetch = withContext(Dispatchers.IO) {
        val url = "$BASE_URL/persona/graph".toHttpUrl().newBuilder()
            .apply {
                if (minSimilarity != null) addQueryParameter("min_similarity", minSimilarity.toString())
                if (clusters) addQueryParameter("clusters", "true")
            }
            .build()
        val request = Request.Builder().url(url).get()
            .apply { if (ifNoneMatch != null) header("If-None-Match", ifNoneMatch) }
            .build()
        client.newCall(request).execute().use { response ->
            val hint = response.header("X-Nova-Consolidation")?.let {
                ConsolidationHint(it, response.header("X-Nova-Last-Consolidated"))
            }
            if (response.code == 304) return@withContext GraphFetch(null, hint)
            val json = JSONObject(response.requireBody())
            GraphFetch(TaggedGraph(graph = parseKnowledgeGraph(json), etag = response.header("ETag")), hint)
        }
    }

    /** One search hit: [keyword] when it matched the query's words rather than its meaning. */
    data class SearchHit(
        val factId: String,
        val score: Float,
        val similarity: Float,
        val keyword: Boolean,
        val clusterId: String?,
    )

    /** GET /persona/search's answer. [targetCluster] is the group the hits concentrate in. */
    data class PersonaSearch(val hits: List<SearchHit>, val targetCluster: String?)

    /**
     * Searches beliefs by meaning and by keyword (the server's hybrid search). Null when the
     * server has no search endpoint yet - the caller falls back to matching on the phone. On
     * such a server the path lands on PATCH/DELETE /persona/{id}, so that's a 405, not a 404.
     */
    suspend fun searchPersona(query: String, limit: Int = 20): PersonaSearch? = withContext(Dispatchers.IO) {
        val url = "$BASE_URL/persona/search".toHttpUrl().newBuilder()
            .addQueryParameter("q", query)
            .addQueryParameter("limit", limit.toString())
            .build()
        client.newCall(Request.Builder().url(url).get().build()).execute().use { response ->
            if (response.code == 404 || response.code == 405) return@withContext null
            parsePersonaSearch(JSONObject(response.requireBody()))
        }
    }

    /** What an automatic consolidation run did. [ran] is false when the server said it wasn't due. */
    data class ConsolidationRun(val ran: Boolean, val derived: Int, val stated: Int)

    // A run reads new episodes, phrases trends and judges each new belief: minutes, not seconds.
    private val slowClient by lazy { client.newBuilder().readTimeout(120, TimeUnit.SECONDS).build() }

    /**
     * Asks the server to learn from recent activity if it's due. Called by the background worker
     * (knowledge/ConsolidationWorker), never from the UI - there is no button any more. A server
     * that predates automatic consolidation ignores `if_due` and runs a full pass, which is what
     * the old button did.
     */
    suspend fun consolidateIfDue(): ConsolidationRun = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$BASE_URL/persona/consolidate?if_due=true")
            .post(JSONObject().toString().toRequestBody(JSON_MEDIA_TYPE))
            .build()
        slowClient.newCall(request).execute().use { response ->
            val json = JSONObject(response.requireBody())
            ConsolidationRun(
                ran = json.optBoolean("ran", true),
                derived = json.optJSONArray("derived")?.length() ?: 0,
                stated = json.optJSONArray("stated")?.length() ?: 0,
            )
        }
    }

    /**
     * Correct a belief. Re-embeds server-side, so it becomes findable by what it now says.
     * Passing null for a field leaves it unchanged. Returns the ids of other beliefs the edit
     * overruled (the server removes a contradicted belief when the edit is newer) - empty from
     * servers that don't report them.
     */
    suspend fun editFact(id: String, text: String?, category: List<String>?): List<String> =
        withContext(Dispatchers.IO) {
            val body = JSONObject().apply {
                if (text != null) put("text", text)
                if (category != null) put("category", JSONArray(category))
            }
            val request = Request.Builder()
                .url("$BASE_URL/persona/$id")
                .patch(body.toString().toRequestBody(JSON_MEDIA_TYPE))
                .build()
            client.newCall(request).execute().use { response ->
                val json = JSONObject(response.requireBody())
                json.optJSONArray("superseded").mapObjects { it.optString("id") }
                    .filter { it.isNotEmpty() && it != id }
            }
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

    /** GET /persona/graph's body. Also how the phone's saved copy is read back (KnowledgeRepository). */
    internal fun parseKnowledgeGraph(json: JSONObject): KnowledgeGraph = KnowledgeGraph(
        nodes = json.optJSONArray("nodes").mapObjects { it.toGraphNode() },
        edges = json.optJSONArray("edges").mapObjects { it.toGraphEdge() },
    )

    /** The inverse of [parseKnowledgeGraph]: the server's own shape, so one parser reads both. */
    internal fun KnowledgeGraph.toJson(): JSONObject = JSONObject().apply {
        put("nodes", JSONArray(nodes.map { n ->
            JSONObject().apply {
                put("id", n.id)
                put("label", n.label)
                put("kind", n.kind)
                put("source", n.source ?: JSONObject.NULL)
                put("confidence", n.confidence?.toDouble() ?: JSONObject.NULL)
                put("category", JSONArray(n.category))
                put("support", n.support ?: JSONObject.NULL)
                put("detail", n.detail ?: JSONObject.NULL)
                put("note_id", n.noteId ?: JSONObject.NULL)
                put("cluster", n.clusterId ?: JSONObject.NULL)
                put("size", n.size ?: JSONObject.NULL)
                put("stated_at", n.statedAt ?: JSONObject.NULL)
            }
        }))
        put("edges", JSONArray(edges.map { e ->
            JSONObject().apply {
                put("source", e.source)
                put("target", e.target)
                put("kind", e.kind)
                put("weight", e.weight.toDouble())
            }
        }))
    }

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
        noteId = if (isNull("note_id")) null else optString("note_id").takeIf { it.isNotEmpty() },
        // isNull is also true for a missing key, so a server without these reads as null.
        clusterId = if (isNull("cluster")) null else optString("cluster").takeIf { it.isNotEmpty() },
        size = if (isNull("size")) null else optInt("size"),
        statedAt = if (isNull("stated_at")) null else optString("stated_at").takeIf { it.isNotEmpty() },
    )

    internal fun parsePersonaSearch(json: JSONObject): PersonaSearch = PersonaSearch(
        hits = json.optJSONArray("hits").mapObjects {
            SearchHit(
                factId = it.getString("fact_id"),
                score = it.optDouble("score", 0.0).toFloat(),
                similarity = it.optDouble("similarity", 0.0).toFloat(),
                keyword = it.optString("match") == "keyword",
                clusterId = if (it.isNull("cluster_id")) null else "cluster:" + it.getString("cluster_id"),
            )
        },
        targetCluster = if (json.isNull("target_cluster")) null else "cluster:" + json.getString("target_cluster"),
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
        // The server's cheap "time to learn from recent activity" check (EventOut.consolidation_due).
        // The phone does the run, in the background, because Cloud Run starves work that
        // outlives a response.
        if (json.optBoolean("consolidation_due", false)) KnowledgeRepository.consolidationDue()
        return when (json.optString("status", "final")) {
            "need_more" -> {
                val req = json.optJSONObject("request") ?: JSONObject()
                EventResult.NeedMore(
                    sessionId = json.getString("session_id"),
                    requestType = req.optString("type"),
                    fromIso = req.optString("from"),
                    toIso = req.optString("to"),
                    includeDone = req.optBoolean("include_done", false),
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
                reminderActions = parseReminderActions(json.optJSONArray("actions")),
                updateReminderActions = parseUpdateReminderActions(json.optJSONArray("actions")),
                recallActions = parseRecallActions(json.optJSONArray("actions")),
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
        // Voice turns only (ReminderRepository.attachWindow) - an ambient snapshot has no
        // total and so sends no reminder text at all.
        if (remindersPendingTotal != null) {
            put("reminders", reminders.toReminderJsonArray())
            put("reminders_pending_total", remindersPendingTotal)
        }
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
