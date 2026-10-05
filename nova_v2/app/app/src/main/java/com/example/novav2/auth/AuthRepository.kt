package com.example.novav2.auth

import android.content.Context
import android.content.Intent
import com.example.novav2.ble.NovaDevicePairing
import com.example.novav2.network.NovaApiClient
import com.example.novav2.network.NovaHttp
import com.example.novav2.network.NovaHttp.withNovaAuth
import com.example.novav2.service.SignalMonitorService
import com.example.novav2.widget.WidgetUpdater
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * The one owner of the signed-in session.
 *
 * Sign-up, sign-in, refresh and sign-out all go to this app's own server (/auth/..., see
 * server/app/api/auth.py), never to Supabase directly. The UI and services only see [state];
 * the tokens themselves only leave this object through [accessTokenForRequest] and
 * [refreshAfter401], which [NovaHttp] uses to sign every API call.
 *
 * Refresh tokens rotate - each one works once - and the UI, SignalMonitorService and
 * AssistVoiceService can all hit an expired token at the same moment. So refreshes are serialised
 * on [lock], and a caller whose token was already replaced by someone else's refresh just picks up
 * the new one instead of spending the (now used) refresh token again.
 *
 * [init] runs from [com.example.novav2.NovaApplication], before any activity, service or receiver.
 */
object AuthRepository {
    private lateinit var appContext: Context
    private lateinit var store: TokenStore
    private val lock = Any()

    /** For work that must outlive whichever screen started it - signing out, above all. */
    val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    @Volatile
    private var session: Session? = null

    private val _state = MutableStateFlow<SessionState>(SessionState.SignedOut)
    val state: StateFlow<SessionState> = _state.asStateFlow()

    val isSignedIn: Boolean get() = session != null

    /** How long sign-out waits for unsent changes to upload before asking what to do. */
    private const val FLUSH_TIMEOUT_MILLIS = 15_000L

    /** Refresh this long before the access token expires, rather than waiting for a 401. */
    private const val EARLY_REFRESH_SECONDS = 60L

    // Auth calls get their own client: the client key, but no bearer token and no
    // Authenticator, so a failed refresh can never recurse into another refresh.
    // A long read timeout on purpose: a cold server can take most of a minute to answer, and
    // giving up on a refresh the server then completes loses the session - it has already
    // swapped the refresh token for one this phone never received.
    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .dns(NovaHttp.serverDns)
        .addInterceptor(NovaHttp.clientKeyInterceptor)
        .build()
    private val JSON = "application/json".toMediaType()

    // Deleting the account is an ordinary signed-in call (bearer token, refreshed on a 401), so
    // it gets the API's own client setup rather than [http]'s.
    private val signedInHttp by lazy {
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(90, TimeUnit.SECONDS)
            .withNovaAuth()
            .build()
    }

    fun init(context: Context) {
        if (::store.isInitialized) return
        appContext = context.applicationContext
        store = TokenStore(appContext)
        session = store.load()
        publish()
    }

    /** Thrown by the sign-in calls with a message fit to show the user. */
    open class AuthException(message: String) : Exception(message)

    /** Signing out now would lose [count] reminder changes or notes that haven't uploaded. */
    class UnsentChangesException(val count: Int) : Exception("$count changes not uploaded")

    sealed interface SignUpResult {
        data object SignedIn : SignUpResult
        /** Only if email confirmation is switched on in Supabase (it's off for now). */
        data object ConfirmEmail : SignUpResult
    }

    suspend fun signIn(email: String, password: String) = withContext(Dispatchers.IO) {
        adopt(Session.fromJson(post("/auth/login", credentials(email, password))))
    }

    suspend fun signUp(email: String, password: String): SignUpResult = withContext(Dispatchers.IO) {
        val body = post("/auth/signup", credentials(email, password))
        if (body.optBoolean("confirmation_required")) {
            SignUpResult.ConfirmEmail
        } else {
            adopt(Session.fromJson(body))
            SignUpResult.SignedIn
        }
    }

    /**
     * Uploads what's unsent, forgets the session and wipes this phone's personal data
     * ([LocalData]), then revokes the session on the server in the background (best effort -
     * signing out works offline too). [everywhere] also revokes every other device's session.
     *
     * Throws [UnsentChangesException] if something couldn't be uploaded, unless [force] - the UI
     * asks before losing it.
     */
    suspend fun signOut(everywhere: Boolean = false, force: Boolean = false) = withContext(Dispatchers.IO) {
        Log.i(TAG, "signing out (everywhere=$everywhere, force=$force)")
        // Only touch the network if something is actually waiting to upload. Even then, the
        // upload can queue behind a slow sync, and a blocking call can't be cancelled - so wait
        // on it in the background, not here.
        var unsent = LocalData.unsentCount(appContext)
        if (unsent > 0) {
            val flush = CoroutineScope(Dispatchers.IO).async { LocalData.flush(appContext) }
            unsent = withTimeoutOrNull(FLUSH_TIMEOUT_MILLIS) { flush.await() }
                ?: LocalData.unsentCount(appContext)
        }
        Log.i(TAG, "sign-out: $unsent change(s) not uploaded")
        if (unsent > 0 && !force) throw UnsentChangesException(unsent)

        // Signed out on the phone straight away; revoking on the server can take a while (it may
        // need a refresh first, from a cold server), so it happens in the background.
        val revoking = session
        forget()
        LocalData.wipe(appContext)
        // The device is this account's too: the next person signs in and pairs their own.
        NovaDevicePairing.forget(appContext)
        Log.i(TAG, "signed out")
        if (revoking != null) appScope.launch(Dispatchers.IO) { revoke(revoking, everywhere) }
    }

    /**
     * Deletes the account and everything in it, for good (server/app/store/account.py), then
     * wipes this phone the way signing out does. [password] is checked again on the server.
     * Nothing is uploaded first - it would only be deleted. Throws [AuthException] with a message
     * for the user if the server didn't confirm the delete, and the user stays signed in.
     */
    suspend fun deleteAccount(password: String) = withContext(Dispatchers.IO) {
        Log.i(TAG, "deleting the account")
        val request = Request.Builder()
            .url("${NovaApiClient.BASE_URL}/me/delete")
            .post(JSONObject().put("password", password).toString().toRequestBody(JSON))
            .build()
        val response = try {
            signedInHttp.newCall(request).execute()
        } catch (e: IOException) {
            throw AuthException("Can't reach Nova. Check your connection and try again.")
        }
        response.use {
            if (!it.isSuccessful) {
                val detail = runCatching { JSONObject(it.body?.string().orEmpty()).opt("detail") }.getOrNull()
                val code = (detail as? JSONObject)?.optString("code").orEmpty()
                throw AuthException(when {
                    code == "wrong_password" -> "Wrong password."
                    it.code == 429 -> "Too many attempts. Wait a minute and try again."
                    it.code == 401 -> "You've been signed out. Sign in again to delete your account."
                    else -> "Couldn't delete your account right now. Nothing was deleted - try again soon."
                })
            }
        }
        forget()
        LocalData.wipe(appContext)
        NovaDevicePairing.forget(appContext)
        Log.i(TAG, "account deleted")
    }

    /**
     * Tells the server a signed-out session is finished. Best effort: if it fails, the refresh
     * token this phone just forgot still expires on its own. The access token has often expired
     * while the app sat idle, so it's refreshed first - the refresh token is still valid.
     */
    private fun revoke(signedOut: Session, everywhere: Boolean) {
        try {
            val token = if (signedOut.expiresAt - nowSeconds() > EARLY_REFRESH_SECONDS) signedOut.accessToken
                else Session.fromJson(post("/auth/refresh", JSONObject().put("refresh_token", signedOut.refreshToken))).accessToken
            val request = Request.Builder()
                .url("${NovaApiClient.BASE_URL}/auth/logout")
                .header("Authorization", "Bearer $token")
                .post(JSONObject().put("scope", if (everywhere) "global" else "local").toString().toRequestBody(JSON))
                .build()
            http.newCall(request).execute().use { Log.i(TAG, "sign-out: server revoke -> ${it.code}") }
        } catch (e: Exception) {
            Log.w(TAG, "sign-out: couldn't revoke on the server: $e")
        }
    }

    /**
     * The access token to attach to an API call, refreshed first if it's about to expire.
     * Null when signed out. Blocking - called on OkHttp's threads.
     */
    fun accessTokenForRequest(): String? {
        if (!::store.isInitialized) return null
        val current = session ?: return null
        if (current.expiresAt - nowSeconds() > EARLY_REFRESH_SECONDS) return current.accessToken
        // Refresh failed for lack of network: the old token may still have a few seconds left.
        return refreshReplacing(current.accessToken) ?: session?.accessToken
    }

    /**
     * After the server rejected [failedToken] with a 401: a token worth retrying with, or null to
     * give up. Blocking - called from OkHttp's Authenticator.
     */
    fun refreshAfter401(failedToken: String): String? =
        if (::store.isInitialized) refreshReplacing(failedToken) else null

    /** Called when even a freshly refreshed token is rejected: nothing left to try. */
    fun onTokenRejected() {
        forget()
    }

    private fun refreshReplacing(staleToken: String): String? = synchronized(lock) {
        val current = session ?: return null
        // Another thread refreshed while this one waited on the lock.
        if (current.accessToken != staleToken) return current.accessToken
        return try {
            val fresh = Session.fromJson(post("/auth/refresh", JSONObject().put("refresh_token", current.refreshToken)))
            store.save(fresh)
            session = fresh
            publish()
            fresh.accessToken
        } catch (e: RejectedException) {
            // 401 from /auth/refresh: the refresh token is revoked, reused or expired.
            // 429 is only a rate limit, so the session survives it.
            if (e.code == 401) forget()
            null
        } catch (e: IOException) {
            null
        } catch (e: AuthException) {
            null
        }
    }

    /** A sign-in or sign-up (not a refresh): take the session, then settle the phone's data. */
    private suspend fun adopt(fresh: Session) {
        synchronized(lock) {
            store.save(fresh)
            session = fresh
        }
        publish()
        LocalData.onSignedIn(appContext, fresh.userId)
    }

    /**
     * Forgets the session. Personal data stays: [signOut] wipes it deliberately, but a session
     * that dies on its own keeps it for the same person to sync when they sign back in.
     */
    private fun forget() {
        synchronized(lock) {
            session = null
            store.clear()
        }
        publish()
        appContext.stopService(Intent(appContext, SignalMonitorService::class.java))
    }

    private fun publish() {
        _state.value = session?.let { SessionState.SignedIn(it.userId, it.email) } ?: SessionState.SignedOut
        // Every way in or out comes through here - sign-in, sign-out, and a session dying on its
        // own - so the widget never keeps showing reminders to a signed-out phone.
        WidgetUpdater.requestUpdate(appContext)
    }

    private fun credentials(email: String, password: String) =
        JSONObject().put("email", email.trim()).put("password", password)

    private class RejectedException(val code: Int, message: String) : AuthException(message)

    /** POSTs to /auth/..., returning the JSON body or throwing with a message for the user. */
    private fun post(path: String, body: JSONObject): JSONObject {
        val request = Request.Builder()
            .url("${NovaApiClient.BASE_URL}$path")
            .post(body.toString().toRequestBody(JSON))
            .build()
        val response = try {
            http.newCall(request).execute()
        } catch (e: IOException) {
            throw AuthException("Can't reach Nova. Check your connection and try again.")
        }
        response.use {
            val text = it.body?.string().orEmpty()
            if (it.isSuccessful) return JSONObject(text)
            throw RejectedException(it.code, friendlyMessage(it.code, text))
        }
    }

    private fun friendlyMessage(code: Int, body: String): String {
        val detail = runCatching { JSONObject(body).opt("detail") }.getOrNull()
        val errorCode = (detail as? JSONObject)?.optString("code").orEmpty()
        return when {
            errorCode == "invalid_credentials" -> "Wrong email or password."
            errorCode == "user_already_exists" || errorCode == "email_exists" ->
                "There's already an account with that email. Try signing in."
            errorCode == "weak_password" -> "Choose a stronger password."
            code == 429 -> "Too many attempts. Wait a minute and try again."
            // FastAPI's own validation errors: detail is a list.
            detail is JSONArray -> "Enter a valid email, and a password of at least 8 characters."
            code == 403 -> "This version of Nova can't reach the server. Check NOVA_API_KEY."
            code == 404 -> "Sign-in isn't available on this server yet."
            code >= 500 -> "Sign-in is unavailable right now. Try again soon."
            else -> (detail as? JSONObject)?.optString("message")?.takeIf { it.isNotBlank() }
                ?: "Sign-in failed ($code)."
        }
    }

    private fun nowSeconds() = System.currentTimeMillis() / 1000

    private const val TAG = "AuthRepository"
}
