package com.example.novav2.auth

import org.json.JSONObject

/**
 * A signed-in session, as the server's /auth/login, /auth/signup and /auth/refresh return it
 * (server/app/schemas/auth.py's Session). Only ever held by [AuthRepository] and persisted,
 * encrypted, by [TokenStore].
 */
data class Session(
    val accessToken: String,
    val refreshToken: String,
    /** Unix seconds when [accessToken] expires. */
    val expiresAt: Long,
    val userId: String,
    val email: String?,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("access_token", accessToken)
        .put("refresh_token", refreshToken)
        .put("expires_at", expiresAt)
        .put("user", JSONObject().put("id", userId).put("email", email ?: JSONObject.NULL))

    companion object {
        fun fromJson(json: JSONObject): Session {
            val user = json.getJSONObject("user")
            return Session(
                accessToken = json.getString("access_token"),
                refreshToken = json.getString("refresh_token"),
                expiresAt = json.getLong("expires_at"),
                userId = user.getString("id"),
                email = user.optString("email").takeUnless { user.isNull("email") || it.isEmpty() },
            )
        }
    }
}

/** What the UI and services see: whether someone is signed in, never the tokens themselves. */
sealed interface SessionState {
    data object SignedOut : SessionState
    data class SignedIn(val userId: String, val email: String?) : SessionState
}
