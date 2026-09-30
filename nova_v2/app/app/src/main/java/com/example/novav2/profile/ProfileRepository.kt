package com.example.novav2.profile

import android.content.Context
import android.util.Log
import com.example.novav2.auth.AuthRepository
import com.example.novav2.auth.SessionState
import com.example.novav2.model.OnboardingAnswers
import com.example.novav2.model.UserAccountProfile
import com.example.novav2.network.ProfileApiClient
import com.example.novav2.state.TravelModePreference
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.io.IOException

/**
 * The signed-in user's profile and onboarding answers, and
 * the gate NovaApp puts in front of the app: onboarding shows until the profile says it's done.
 *
 * - GET /me's answer is cached per user, so an offline cold start goes straight into the app
 *   instead of back through onboarding. The cache is refreshed in the background on every start.
 * - Onboarding's answers are saved as a draft at every step, so a failed final POST - or the app
 *   being killed half way through - loses nothing.
 * - The travel-mode answer is copied into [TravelModePreference], which is what every /event's
 *   user_state carries; Settings' travel row writes both.
 *
 * Cleared on sign-out with everything else personal ([com.example.novav2.auth.LocalData.wipe]).
 */
object ProfileRepository {
    private const val TAG = "ProfileRepository"
    private const val PREFS = "nova_profile"
    private const val KEY_USER_ID = "user_id"
    private const val KEY_PROFILE = "profile"         // JSON, or absent for "no profile yet"
    private const val KEY_HAS_CACHE = "has_cache"
    private const val DRAFT_PREFS = "nova_onboarding_draft"
    private const val KEY_DRAFT_NAME = "name"
    private const val KEY_DRAFT_ANSWERS = "answers"
    private const val KEY_DRAFT_STEP = "step"

    sealed interface ProfileState {
        /** Signed in, nothing cached, asking the server. */
        data object Loading : ProfileState
        /** Nothing cached and the server couldn't be reached. */
        data class Unavailable(val message: String) : ProfileState
        /** [profile] is null before the user has been through onboarding. */
        data class Ready(val profile: UserAccountProfile?) : ProfileState
    }

    private val _state = MutableStateFlow<ProfileState>(ProfileState.Loading)
    val state: StateFlow<ProfileState> = _state.asStateFlow()

    /** "Continue anyway" on the can't-reach-the-server screen: into the app for this run only. */
    private val _skipped = MutableStateFlow(false)
    val skippedThisRun: StateFlow<Boolean> = _skipped.asStateFlow()

    private lateinit var app: Context
    private var userId: String? = null

    fun init(context: Context) {
        app = context.applicationContext
        AuthRepository.appScope.launch {
            AuthRepository.state.collect { session ->
                when (session) {
                    is SessionState.SignedIn -> if (session.userId != userId) load(session.userId)
                    SessionState.SignedOut -> {
                        userId = null
                        _skipped.value = false
                        _state.value = ProfileState.Loading
                    }
                }
            }
        }
    }

    private fun load(forUser: String) {
        userId = forUser
        _skipped.value = false
        val prefs = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val cached = if (prefs.getString(KEY_USER_ID, null) == forUser && prefs.getBoolean(KEY_HAS_CACHE, false)) {
            ProfileState.Ready(prefs.getString(KEY_PROFILE, null)?.let { UserAccountProfile.fromJson(JSONObject(it)) })
        } else null
        _state.value = cached ?: ProfileState.Loading
        refresh()
    }

    /** Ask the server again - on start-up, and from the can't-reach-the-server screen. */
    fun refresh() {
        val forUser = userId ?: return
        AuthRepository.appScope.launch {
            if (_state.value is ProfileState.Unavailable) _state.value = ProfileState.Loading
            try {
                val profile = ProfileApiClient.getProfile()
                if (userId == forUser) adopt(forUser, profile)
            } catch (e: IOException) {
                Log.w(TAG, "profile refresh failed: ${e.message}")
                if (userId == forUser && _state.value is ProfileState.Loading) {
                    _state.value = ProfileState.Unavailable("Couldn't reach Nova to load your profile.")
                }
            }
        }
    }

    fun skipForThisRun() {
        _skipped.value = true
    }

    /** The end of onboarding. Throws IOException if it couldn't be saved; the draft is kept. */
    suspend fun completeOnboarding(displayName: String?, answers: OnboardingAnswers) {
        val forUser = userId ?: throw IOException("not signed in")
        val profile = ProfileApiClient.completeOnboarding(displayName?.trim()?.ifEmpty { null }, answers)
        adopt(forUser, profile)
        clearDraft()
    }

    /**
     * Settings -> Your profile. Sends only what differs from the saved profile. Throws IOException
     * if it couldn't be saved.
     */
    suspend fun update(displayName: String?, answers: OnboardingAnswers) {
        val forUser = userId ?: throw IOException("not signed in")
        val current = (state.value as? ProfileState.Ready)?.profile
        val patch = JSONObject()
        val name = displayName?.trim()?.ifEmpty { null }
        if (name != current?.displayName) patch.put("display_name", name ?: JSONObject.NULL)
        val before = (current?.answers ?: OnboardingAnswers()).toJson()
        val after = answers.toJson()
        val changed = JSONObject()
        after.keys().forEach { key ->
            if ("${after.opt(key)}" != "${before.opt(key)}") changed.put(key, after.get(key))
        }
        if (changed.length() > 0) patch.put("answers", changed)
        if (patch.length() == 0) return
        adopt(forUser, ProfileApiClient.updateProfile(patch))
    }

    /** Settings' travel row: keep the account's answer in step. Best-effort - the phone's own
     * setting is already saved, and that's what each turn sends. */
    fun updateTravelMode(mode: String) {
        if (userId == null) return
        val current = (state.value as? ProfileState.Ready)?.profile ?: return
        AuthRepository.appScope.launch {
            try {
                update(current.displayName, current.answers.copy(travelMode = mode))
            } catch (e: IOException) {
                Log.w(TAG, "travel mode not saved to the account: ${e.message}")
            }
        }
    }

    private fun adopt(forUser: String, profile: UserAccountProfile?) {
        val editor = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(KEY_USER_ID, forUser)
            .putBoolean(KEY_HAS_CACHE, true)
        if (profile != null) editor.putString(KEY_PROFILE, profile.toJson().toString()) else editor.remove(KEY_PROFILE)
        editor.apply()
        profile?.answers?.travelMode?.let { TravelModePreference.set(app, it) }
        _state.value = ProfileState.Ready(profile)
    }

    // --- the onboarding draft ---------------------------------------------------------

    data class Draft(val displayName: String, val answers: OnboardingAnswers, val step: Int)

    fun draft(): Draft {
        val prefs = app.getSharedPreferences(DRAFT_PREFS, Context.MODE_PRIVATE)
        if (prefs.getString(KEY_USER_ID, null) != userId) return Draft("", OnboardingAnswers(), 0)
        return Draft(
            displayName = prefs.getString(KEY_DRAFT_NAME, "").orEmpty(),
            answers = prefs.getString(KEY_DRAFT_ANSWERS, null)
                ?.let { OnboardingAnswers.fromJson(JSONObject(it)) } ?: OnboardingAnswers(),
            step = prefs.getInt(KEY_DRAFT_STEP, 0),
        )
    }

    fun saveDraft(draft: Draft) {
        app.getSharedPreferences(DRAFT_PREFS, Context.MODE_PRIVATE).edit()
            .putString(KEY_USER_ID, userId)
            .putString(KEY_DRAFT_NAME, draft.displayName)
            .putString(KEY_DRAFT_ANSWERS, draft.answers.toJson().toString())
            .putInt(KEY_DRAFT_STEP, draft.step)
            .apply()
    }

    private fun clearDraft() {
        app.getSharedPreferences(DRAFT_PREFS, Context.MODE_PRIVATE).edit().clear().apply()
    }

    /** Sign-out: forget the cached profile and any half-finished onboarding. */
    fun clear(context: Context) {
        val ctx = context.applicationContext
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply()
        ctx.getSharedPreferences(DRAFT_PREFS, Context.MODE_PRIVATE).edit().clear().apply()
    }
}
