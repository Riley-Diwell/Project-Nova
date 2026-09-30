package com.example.novav2.network

import com.example.novav2.network.NovaHttp.withNovaAuth
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * POST /reminders/sync (server/app/api/reminders.py): send this phone's unsent reminder changes,
 * get back everything that changed on the account since [since]. The reminder bodies are opaque
 * here - [com.example.novav2.state.ReminderSync] maps them to and from Room.
 */
object RemindersApiClient {
    private val JSON = "application/json".toMediaType()

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .withNovaAuth()
        .build()

    data class SyncResult(val reminders: List<JSONObject>, val cursor: String?)

    /** [changes] are items of {id, status, updated_at_ms, data}. Throws IOException for
     * anything worth retrying later, including an HTTP error. */
    suspend fun sync(since: String?, changes: JSONArray): SyncResult = withContext(Dispatchers.IO) {
        val body = JSONObject()
            .put("since", since ?: JSONObject.NULL)
            .put("changes", changes)
        val request = Request.Builder()
            .url("${NovaApiClient.BASE_URL}/reminders/sync")
            .post(body.toString().toRequestBody(JSON))
            .build()
        client.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) throw IOException("HTTP ${response.code}: ${text.take(200)}")
            val json = JSONObject(text)
            val rows = json.getJSONArray("reminders")
            SyncResult(
                reminders = (0 until rows.length()).map { rows.getJSONObject(it) },
                cursor = json.optString("cursor").takeUnless { json.isNull("cursor") || it.isEmpty() },
            )
        }
    }
}
