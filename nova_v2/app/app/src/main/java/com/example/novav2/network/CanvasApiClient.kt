package com.example.novav2.network

import com.example.novav2.network.NovaHttp.withNovaAuth
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject

/**
 * The server's /canvas surface (nova_v2/server/app/api/canvas.py): connect the user's Canvas
 * with a personal access token, see whether it's connected, disconnect it.
 *
 * The token goes to the Nova server once, on connect, and is never kept on the phone or sent
 * back - [Status] carries the Canvas address and name only. The server does the Canvas reading
 * itself, when the user asks Nova something about their courses.
 */
object CanvasApiClient {
    private val JSON = "application/json".toMediaType()

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        // Connecting waits on the server's own check against Canvas.
        .readTimeout(30, TimeUnit.SECONDS)
        .withNovaAuth()
        .build()

    private fun url() = "${NovaApiClient.BASE_URL}/canvas"

    data class Status(
        val connected: Boolean,
        val baseUrl: String? = null,
        val canvasUserName: String? = null,
    ) {
        /** "canvas.uni.edu.au" - the address without the scheme, for display. */
        val host: String? get() = baseUrl?.removePrefix("https://")
    }

    /** A failure worth showing as-is: the server's own explanation (bad token, bad address). */
    class CanvasException(message: String) : IOException(message)

    private val _status = MutableStateFlow<Status?>(null)

    /** Last known status; null until the first [refresh]. Settings and onboarding share it. */
    val status: StateFlow<Status?> = _status

    suspend fun refresh(): Status = withContext(Dispatchers.IO) {
        val request = Request.Builder().url(url()).get().build()
        client.newCall(request).execute().use { parse(it) }.also { _status.value = it }
    }

    suspend fun connect(address: String, token: String): Status = withContext(Dispatchers.IO) {
        val body = JSONObject().put("base_url", address.trim()).put("token", token.trim())
        val request = Request.Builder().url(url()).put(body.toString().toRequestBody(JSON)).build()
        client.newCall(request).execute().use { parse(it) }.also { _status.value = it }
    }

    suspend fun disconnect() = withContext(Dispatchers.IO) {
        val request = Request.Builder().url(url()).delete().build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw IOException("Backend returned ${response.code}")
        }
        _status.value = Status(connected = false)
    }

    private fun parse(response: Response): Status {
        val text = response.body?.string().orEmpty()
        if (!response.isSuccessful) {
            // FastAPI: {"detail": "..."} for ours, {"detail": [...]} for a body that failed
            // validation (a token far too short, say).
            val detail = runCatching { JSONObject(text).opt("detail") }.getOrNull()
            throw when {
                detail is String && response.code in 400..499 -> CanvasException(detail)
                response.code == 422 -> CanvasException("Check the address and the token - one of them isn't right.")
                response.code == 503 -> CanvasException("The Nova server can't store Canvas details right now.")
                else -> IOException("Backend returned ${response.code}")
            }
        }
        val json = JSONObject(text)
        return Status(
            connected = json.optBoolean("connected"),
            baseUrl = json.optString("base_url").takeIf { it.isNotBlank() && it != "null" },
            canvasUserName = json.optString("canvas_user_name").takeIf { it.isNotBlank() && it != "null" },
        )
    }
}
