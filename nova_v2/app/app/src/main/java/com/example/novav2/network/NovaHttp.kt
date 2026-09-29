package com.example.novav2.network

import com.example.novav2.BuildConfig
import com.example.novav2.auth.AuthRepository
import okhttp3.Authenticator
import okhttp3.Dns
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import java.net.InetAddress
import java.net.UnknownHostException

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

    /** The server's tailnet address, for when its name can't be looked up. Both from
     *  local.properties - see NOVA_BASE_URL / NOVA_SERVER_IP in app/build.gradle.kts. */
    private val SERVER_HOST = NovaApiClient.BASE_URL.toHttpUrlOrNull()?.host
    private val SERVER_TAILNET_IP = BuildConfig.NOVA_SERVER_IP

    /**
     * The system resolver first; if it can't find the server's tailnet name, its fixed tailnet
     * address. The Android emulator can't use Tailscale's MagicDNS, though it routes to the
     * tailnet fine through the host, so without this it can't reach the server at all. TLS is
     * still checked against [SERVER_HOST] - only the lookup changes.
     */
    val serverDns: Dns = object : Dns {
        override fun lookup(hostname: String): List<InetAddress> = try {
            Dns.SYSTEM.lookup(hostname)
        } catch (e: UnknownHostException) {
            if (hostname != SERVER_HOST || SERVER_TAILNET_IP.isEmpty()) throw e
            listOf(InetAddress.getByName(SERVER_TAILNET_IP))
        }
    }

    fun OkHttpClient.Builder.withNovaAuth(): OkHttpClient.Builder = this
        .dns(serverDns)
        .addInterceptor(clientKeyInterceptor)
        .addInterceptor(bearerInterceptor)
        .authenticator(tokenAuthenticator)
}
