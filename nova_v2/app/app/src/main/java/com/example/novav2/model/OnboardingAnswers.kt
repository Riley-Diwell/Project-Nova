package com.example.novav2.model

import org.json.JSONArray
import org.json.JSONObject

/**
 * What onboarding asks - the phone's copy of the server's
 * OnboardingAnswers (server/app/schemas/profile.py), field for field and in the same JSON.
 * Every answer is optional: null, or an empty list, means skipped.
 *
 * Times are "HH:mm" on the user's own clock.
 */
data class OnboardingAnswers(
    val campusDays: List<String> = emptyList(),
    val classTimes: List<String> = emptyList(),
    val timetableInCalendar: String? = null,
    val sleepStart: String? = null,
    val sleepEnd: String? = null,
    val travelMode: String? = null,
    val focusInterruptions: String? = null,
    val proactivity: String? = null,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("version", VERSION)
        .put("campus_days", JSONArray(campusDays))
        .put("class_times", JSONArray(classTimes))
        .put("timetable_in_calendar", timetableInCalendar ?: JSONObject.NULL)
        .put("sleep_start", sleepStart ?: JSONObject.NULL)
        .put("sleep_end", sleepEnd ?: JSONObject.NULL)
        .put("travel_mode", travelMode ?: JSONObject.NULL)
        .put("focus_interruptions", focusInterruptions ?: JSONObject.NULL)
        .put("proactivity", proactivity ?: JSONObject.NULL)

    companion object {
        /** The questionnaire this build asks. See [UserAccountProfile.needsOnboarding]. */
        const val VERSION = 1

        // Display order and labels. The keys are the server's values.
        val DAYS = listOf("mon" to "Mon", "tue" to "Tue", "wed" to "Wed", "thu" to "Thu",
            "fri" to "Fri", "sat" to "Sat", "sun" to "Sun")
        val CLASS_TIMES = listOf("morning" to "Morning", "afternoon" to "Afternoon", "evening" to "Evening")
        val TIMETABLE = listOf("yes" to "Yes", "partly" to "Partly", "no" to "No")
        /** navigation_departure_time's modes, like Settings' travel row. */
        val TRAVEL_MODES = listOf("transit" to "Transit", "walking" to "Walking", "driving" to "Driving")
        val FOCUS = listOf(
            "urgent_only" to "Urgent things only",
            "important" to "Important things",
            "anything_useful" to "Anything useful",
        )
        val PROACTIVITY = listOf(
            "only_when_asked" to "Only when I ask",
            "a_little" to "A little (recommended)",
            "proactive" to "Proactive",
        )

        /** The time pickers' starting point when a sleep time hasn't been given. */
        const val DEFAULT_SLEEP_START = "23:00"
        const val DEFAULT_SLEEP_END = "07:00"

        fun fromJson(json: JSONObject?): OnboardingAnswers {
            if (json == null) return OnboardingAnswers()
            return OnboardingAnswers(
                campusDays = json.stringList("campus_days"),
                classTimes = json.stringList("class_times"),
                timetableInCalendar = json.stringOrNull("timetable_in_calendar"),
                // The server sends "22:30:00"; the phone works in "HH:mm".
                sleepStart = json.stringOrNull("sleep_start")?.take(5),
                sleepEnd = json.stringOrNull("sleep_end")?.take(5),
                travelMode = json.stringOrNull("travel_mode"),
                focusInterruptions = json.stringOrNull("focus_interruptions"),
                proactivity = json.stringOrNull("proactivity"),
            )
        }

        private fun JSONObject.stringList(key: String): List<String> {
            val array = optJSONArray(key) ?: return emptyList()
            return (0 until array.length()).map { array.getString(it) }
        }

        private fun JSONObject.stringOrNull(key: String): String? =
            if (has(key) && !isNull(key)) getString(key).takeIf { it.isNotEmpty() } else null
    }
}

/**
 * The signed-in account's profile, as GET /me returns it. Named apart from [UserProfile], the old
 * in-memory onboarding state the Dashboard still reads.
 */
data class UserAccountProfile(
    val displayName: String?,
    val onboardingVersion: Int,
    val onboardingCompletedAt: String?,
    val answers: OnboardingAnswers,
) {
    /** Onboarding shows until it's been finished (or skipped through) at this build's version,
     * so a question added later is asked once. */
    val needsOnboarding: Boolean
        get() = onboardingCompletedAt == null || onboardingVersion < OnboardingAnswers.VERSION

    fun toJson(): JSONObject = JSONObject()
        .put("display_name", displayName ?: JSONObject.NULL)
        .put("onboarding_version", onboardingVersion)
        .put("onboarding_completed_at", onboardingCompletedAt ?: JSONObject.NULL)
        .put("answers", answers.toJson())

    companion object {
        fun fromJson(json: JSONObject): UserAccountProfile = UserAccountProfile(
            displayName = json.optString("display_name").takeUnless { json.isNull("display_name") || it.isEmpty() },
            onboardingVersion = json.optInt("onboarding_version", 0),
            onboardingCompletedAt = json.optString("onboarding_completed_at")
                .takeUnless { json.isNull("onboarding_completed_at") || it.isEmpty() },
            answers = OnboardingAnswers.fromJson(json.optJSONObject("answers")),
        )
    }
}
