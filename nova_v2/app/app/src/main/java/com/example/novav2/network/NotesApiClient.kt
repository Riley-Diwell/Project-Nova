package com.example.novav2.network

import com.example.novav2.notes.CapturedNote
import java.io.IOException
import java.time.Instant
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import com.example.novav2.network.NovaHttp.withNovaAuth
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject

/**
 * The server's /notes surface (nova_v2/server/app/api/notes.py).
 * A separate object from [NovaApiClient] on purpose - the notes feature owns this file, and the
 * two share only the server address and API key.
 *
 * Every call throws [IOException] when the server can't be reached and [NotesHttpException]
 * for an HTTP error, so callers can tell "retry later" (network, 5xx) from "never going to
 * work" (4xx) - the outbox depends on that distinction.
 */
object NotesApiClient {
    private val JSON = "application/json".toMediaType()

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        // POST /notes/{id}/summarise can take the server's full 45 s summary budget.
        .readTimeout(60, TimeUnit.SECONDS)
        .withNovaAuth()
        .build()

    private fun url(path: String) = "${NovaApiClient.BASE_URL}$path"

    class NotesHttpException(val code: Int, message: String) : IOException("HTTP $code: $message") {
        /** Worth retrying: the server or something in front of it is having a bad time, or
         * (401) the sign-in lapsed - the note waits in the outbox until the user signs back in. */
        val retryable: Boolean get() = code >= 500 || code == 408 || code == 429 || code == 401
    }

    // --- models (wire shapes, see api/notes.py and store/notes/models.py) -------------

    data class FlaggedMoment(val tS: Double, val quote: String)

    data class Summary(
        val title: String,
        val tldr: String,
        val keyPoints: List<String>,
        val actionItems: List<String>,
        val openQuestions: List<String>,
        val flaggedMoments: List<FlaggedMoment>,
    )

    data class Segment(val startS: Double, val endS: Double, val text: String)

    data class Note(
        val id: String,
        val createdAt: Instant,
        val updatedAt: Instant,
        val source: String,
        val kind: String,
        val title: String?,
        val text: String,
        val segments: List<Segment>,
        val durationS: Double?,
        val calendarTitle: String?,
        val sttEngine: String?,
        val tags: List<String>,
        val summary: Summary?,
        val summaryStatus: String,
        val promotedFactIds: List<String>,
        /** For a spoken note: what Nova thinks was meant, shown under [text] as heard
         * (server notes_pipeline/interpret.py). Null until read, or if nothing changed. */
        val interpretedText: String? = null,
    ) {
        val displayTitle: String
            get() = title?.takeIf { it.isNotBlank() }
                ?: summary?.title?.takeIf { it.isNotBlank() }
                ?: text.split(Regex("\\s+")).take(8).joinToString(" ")
    }

    data class NoteRow(
        val id: String,
        val createdAt: Instant,
        val source: String,
        val kind: String,
        val title: String,
        val preview: String,
        val snippet: String?,
        val snippetStartS: Double?,
        val tldr: String?,
        val durationS: Double?,
        val calendarTitle: String?,
        val summaryStatus: String,
        val promoted: Boolean,
    )

    // --- calls -------------------------------------------------------------------

    suspend fun create(note: CapturedNote): Note = createRaw(note.toJson().toString())

    /** POST an already-serialised body - the outbox resends exactly what it stored. */
    suspend fun createRaw(payload: String): Note = withContext(Dispatchers.IO) {
        val request = Request.Builder().url(url("/notes")).post(payload.toRequestBody(JSON)).build()
        client.newCall(request).execute().use { parseNote(JSONObject(it.bodyOrThrow())) }
    }

    /** List (no [query]) or search. [since]/[until] are LOCAL ISO times ("2026-09-23T00:00:00"),
     * read against [utcOffsetMinutes] server-side - the same shape the memory tool's recall
     * uses, which is what lets a recall chip re-run the assistant's search. */
    suspend fun list(
        query: String? = null,
        kind: String? = null,
        since: String? = null,
        until: String? = null,
        utcOffsetMinutes: Int? = null,
        limit: Int = 100,
    ): List<NoteRow> = withContext(Dispatchers.IO) {
        val httpUrl = url("/notes").toHttpUrl().newBuilder().apply {
            query?.takeIf { it.isNotBlank() }?.let { addQueryParameter("q", it) }
            kind?.let { addQueryParameter("kind", it) }
            since?.let { addQueryParameter("since", it) }
            until?.let { addQueryParameter("until", it) }
            utcOffsetMinutes?.let { addQueryParameter("utc_offset_minutes", it.toString()) }
            addQueryParameter("limit", limit.toString())
        }.build()
        client.newCall(Request.Builder().url(httpUrl).get().build()).execute().use {
            parseRows(JSONArray(it.bodyOrThrow()))
        }
    }

    suspend fun get(id: String): Note = withContext(Dispatchers.IO) {
        client.newCall(Request.Builder().url(url("/notes/$id")).get().build()).execute().use {
            parseNote(JSONObject(it.bodyOrThrow()))
        }
    }

    suspend fun update(id: String, title: String? = null, text: String? = null): Note =
        withContext(Dispatchers.IO) {
            val body = JSONObject().apply {
                title?.let { put("title", it) }
                text?.let { put("text", it) }
            }
            val request = Request.Builder().url(url("/notes/$id"))
                .patch(body.toString().toRequestBody(JSON)).build()
            client.newCall(request).execute().use { parseNote(JSONObject(it.bodyOrThrow())) }
        }

    suspend fun delete(id: String) = withContext(Dispatchers.IO) {
        client.newCall(Request.Builder().url(url("/notes/$id")).delete().build()).execute().use {
            if (it.code != 404) it.bodyOrThrow()  // already gone is what we wanted
        }
    }

    suspend fun deleteAll(): Int = withContext(Dispatchers.IO) {
        val request = Request.Builder().url(url("/notes?confirm=true")).delete().build()
        client.newCall(request).execute().use { JSONObject(it.bodyOrThrow()).optInt("deleted") }
    }

    suspend fun summarise(id: String): Note = withContext(Dispatchers.IO) {
        val request = Request.Builder().url(url("/notes/$id/summarise"))
            .post("".toRequestBody(JSON)).build()
        client.newCall(request).execute().use { parseNote(JSONObject(it.bodyOrThrow())) }
    }

    /** "Add to what Nova knows" - returns the new Persona fact id. */
    suspend fun promote(id: String, category: List<String>): String = withContext(Dispatchers.IO) {
        val body = JSONObject().put("category", JSONArray(category))
        val request = Request.Builder().url(url("/notes/$id/promote"))
            .post(body.toString().toRequestBody(JSON)).build()
        client.newCall(request).execute().use { JSONObject(it.bodyOrThrow()).getString("fact_id") }
    }

    /** One note as Markdown, for Share. */
    suspend fun markdown(id: String): String = withContext(Dispatchers.IO) {
        client.newCall(Request.Builder().url(url("/notes/$id/markdown")).get().build()).execute()
            .use { it.bodyOrThrow() }
    }

    /** Every note, as "json" or "md" text, for Settings -> Export. */
    suspend fun export(format: String): String = withContext(Dispatchers.IO) {
        client.newCall(Request.Builder().url(url("/notes/export?format=$format")).get().build())
            .execute().use { it.bodyOrThrow() }
    }

    private fun Response.bodyOrThrow(): String {
        val text = body?.string().orEmpty()
        if (!isSuccessful) throw NotesHttpException(code, text.take(200))
        return text
    }

    // --- JSON (internal so the JVM tests can reach them) -----------------------------

    internal fun CapturedNote.toJson(): JSONObject = JSONObject().apply {
        put("id", id)
        put("created_at", Instant.ofEpochMilli(createdAtMillis).toString())
        put("source", source)
        put("kind", kind)
        put("text", text)
        title?.let { put("title", it) }
        put("segments", JSONArray().apply {
            segments.forEach { s ->
                put(JSONObject().put("start_s", s.startS).put("end_s", s.endS).put("text", s.text))
            }
        })
        durationS?.let { put("duration_s", it) }
        if (calendarTitle != null || location != null) {
            put("context", JSONObject().apply {
                calendarTitle?.let { put("calendar_title", it) }
                location?.let { put("location", it) }
            })
        }
        if (sttEngine != null) {
            put("stt", JSONObject().apply {
                put("engine", sttEngine)
                sttAvgConf?.let { put("avg_conf", it) }
            })
        }
        put("tags", JSONArray(tags))
        // The phone asks for the summary separately (so the save itself confirms fast - one
        // buzz, then two more when the summary lands), so creation never waits on the model.
        put("summarise", "never")
    }

    internal fun parseNote(o: JSONObject): Note = Note(
        id = o.getString("id"),
        createdAt = Instant.parse(o.getString("created_at").normaliseIso()),
        updatedAt = Instant.parse(o.getString("updated_at").normaliseIso()),
        source = o.optString("source"),
        kind = o.optString("kind"),
        title = o.optStringOrNull("title"),
        text = o.optString("text"),
        segments = o.optJSONArray("segments").objects().map {
            Segment(it.optDouble("start_s"), it.optDouble("end_s"), it.optString("text"))
        },
        durationS = o.optDoubleOrNull("duration_s"),
        calendarTitle = o.optJSONObject("context")?.optStringOrNull("calendar_title"),
        sttEngine = o.optJSONObject("stt")?.optStringOrNull("engine"),
        tags = o.optJSONArray("tags").strings(),
        summary = o.optJSONObject("summary")?.let { s ->
            Summary(
                title = s.optString("title"),
                tldr = s.optString("tldr"),
                keyPoints = s.optJSONArray("key_points").strings(),
                actionItems = s.optJSONArray("action_items").strings(),
                openQuestions = s.optJSONArray("open_questions").strings(),
                flaggedMoments = s.optJSONArray("flagged_moments").objects().map {
                    FlaggedMoment(it.optDouble("t_s"), it.optString("quote"))
                },
            )
        },
        summaryStatus = o.optString("summary_status", "none"),
        promotedFactIds = o.optJSONArray("promoted_fact_ids").strings(),
        interpretedText = o.optStringOrNull("interpreted_text"),
    )

    internal fun parseRows(a: JSONArray): List<NoteRow> = a.objects().map { o ->
        NoteRow(
            id = o.getString("id"),
            createdAt = Instant.parse(o.getString("created_at").normaliseIso()),
            source = o.optString("source"),
            kind = o.optString("kind"),
            title = o.optString("title"),
            preview = o.optString("preview"),
            snippet = o.optStringOrNull("snippet"),
            snippetStartS = o.optDoubleOrNull("snippet_start_s"),
            tldr = o.optStringOrNull("tldr"),
            durationS = o.optDoubleOrNull("duration_s"),
            calendarTitle = o.optStringOrNull("calendar_title"),
            summaryStatus = o.optString("summary_status", "none"),
            promoted = o.optBoolean("promoted"),
        )
    }

    /** Pydantic writes "2026-09-23T10:05:00Z" or "+00:00" offsets, sometimes without them for
     * a naive stored time; Instant.parse wants a Z or an offset it can read. */
    private fun String.normaliseIso(): String = when {
        endsWith("Z") -> this
        Regex("[+-]\\d{2}:\\d{2}$").containsMatchIn(this) ->
            java.time.OffsetDateTime.parse(this).toInstant().toString()
        else -> this + "Z"
    }

    private fun JSONObject.optStringOrNull(key: String): String? =
        if (isNull(key)) null else optString(key).takeIf { it.isNotEmpty() }

    private fun JSONObject.optDoubleOrNull(key: String): Double? =
        if (isNull(key)) null else optDouble(key).takeUnless { it.isNaN() }

    private fun JSONArray?.objects(): List<JSONObject> =
        if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }

    private fun JSONArray?.strings(): List<String> =
        if (this == null) emptyList() else (0 until length()).map { optString(it) }
}
