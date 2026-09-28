package com.example.novav2.network

import com.example.novav2.BuildConfig
import com.example.novav2.auth.AuthRepository
import okhttp3.Authenticator
import okhttp3.Interceptor
import okhttp3.OkHttpClient

/**
 * What every call to the Nova server carries:
 *
 * - `X-Nova-Api-Key`, the build's client key. Not a secret (it ships in the APK); it only keeps
 *   drive-by traffic out. A wrong key is a 403.
 * - `Authorization: Bearer <access token>` while signed in - the real proof of who's calling.
 *   Refreshed just before it expires, and once more if the server answers 401 anyway.
 *
 * NovaApiClient and NotesApiClient build their clients with [withNovaAuth]; AuthRepository's own
 * client takes only [clientKeyInterceptor], so refreshing can't trigger another refresh.
 */
object NovaHttp {
    private val API_KEY = BuildConfig.NOVA_API_KEY

    val clientKeyInterceptor = Interceptor { chain ->
        val request = if (API_KEY.isEmpty()) chain.request()
            else chain.request().newBuilder().header("X-Nova-Api-Key", API_KEY).build()
        chain.proceed(request)
    }

    private val bearerInterceptor = Interceptor { chain ->
        val token = AuthRepository.accessTokenForRequest()
        val request = if (token == null) chain.request()
            else chain.request().newBuilder().header("Authorization", "Bearer $token").build()
        chain.proceed(request)
    }

    /** Runs on a 401: refresh once and retry; if the retry is rejected too, sign out. */
    private val tokenAuthenticator = Authenticator { _, response ->
        val failed = response.request.header("Authorization")?.removePrefix("Bearer ")
            ?: return@Authenticator null // wasn't signed in - nothing to refresh
        if (response.priorResponse != null) {
            AuthRepository.onTokenRejected()
            return@Authenticator null
        }
        val fresh = AuthRepository.refreshAfter401(failed) ?: return@Authenticator null
        response.request.newBuilder().header("Authorization", "Bearer $fresh").build()
    }

    fun OkHttpClient.Builder.withNovaAuth(): OkHttpClient.Builder = this
        .addInterceptor(clientKeyInterceptor)
        .addInterceptor(bearerInterceptor)
        .authenticator(tokenAuthenticator)
}
