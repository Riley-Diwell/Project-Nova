package com.example.novav2.network

import com.example.novav2.model.OnboardingAnswers
import com.example.novav2.model.UserAccountProfile
import com.example.novav2.network.NovaHttp.withNovaAuth
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * The signed-in user's profile (server/app/api/me.py):
 *
 * - GET /me              -> {user_id, email, profile}; profile is null before onboarding
 * - POST /me/onboarding  {display_name, answers}
 * - PATCH /me/profile    only what changed
 *
 * Every call throws IOException on failure, HTTP errors included, so callers have one thing to
 * catch. [com.example.novav2.profile.ProfileRepository] is the only caller.
 */
object ProfileApiClient {
    private val JSON = "application/json".toMediaType()

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        // Saving onboarding writes Persona facts, which embeds them - slow on a cold server.
        .readTimeout(60, TimeUnit.SECONDS)
        .withNovaAuth()
        .build()

    /** The profile, or null if the user hasn't been through onboarding yet. */
    suspend fun getProfile(): UserAccountProfile? = withContext(Dispatchers.IO) {
        val json = call(Request.Builder().url("${NovaApiClient.BASE_URL}/me").get().build())
        json.optJSONObject("profile")?.let(UserAccountProfile::fromJson)
    }

    suspend fun completeOnboarding(displayName: String?, answers: OnboardingAnswers): UserAccountProfile =
        withContext(Dispatchers.IO) {
            val body = JSONObject()
                .put("display_name", displayName ?: JSONObject.NULL)
                .put("answers", answers.toJson())
            UserAccountProfile.fromJson(call(
                Request.Builder().url("${NovaApiClient.BASE_URL}/me/onboarding")
                    .post(body.toString().toRequestBody(JSON)).build()
            ))
        }

    /** [patch] is {display_name?, answers?: {only the answers that changed}}. */
    suspend fun updateProfile(patch: JSONObject): UserAccountProfile = withContext(Dispatchers.IO) {
        UserAccountProfile.fromJson(call(
            Request.Builder().url("${NovaApiClient.BASE_URL}/me/profile")
                .patch(patch.toString().toRequestBody(JSON)).build()
        ))
    }

    private fun call(request: Request): JSONObject =
        client.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) throw IOException("HTTP ${response.code}: ${text.take(200)}")
            JSONObject(text)
        }
}
