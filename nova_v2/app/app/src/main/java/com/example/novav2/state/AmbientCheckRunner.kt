package com.example.novav2.state

import android.content.Context
import com.example.novav2.model.UserState
import com.example.novav2.network.NovaApiClient
import java.io.IOException

/**
 * Posts an ambient Event and acts on the response - the one place that does this, shared by
 * [com.example.novav2.service.SignalMonitorService]'s periodic cadence and StateScreen's manual
 * "check now" debug button, so both go through the exact same pipeline (notify, schedule,
 * reinforce) instead of the button being a second, drifting copy of it.
 *
 * Callers decide whether to gate this on [hasImminentCommitment] first - the automatic loop
 * does, the manual debug button deliberately doesn't, since its whole point is forcing a real
 * check on demand regardless of whether anything happens to be imminent right now.
 */
object AmbientCheckRunner {

    fun hasImminentCommitment(userState: UserState, horizonMinutes: Int): Boolean =
        (userState.currentEvents + userState.upcomingEvents).any {
            it.minutesUntilStart <= horizonMinutes
        }

    suspend fun run(context: Context, userState: UserState) {
        val result = try {
            val response = NovaApiClient.postAmbientEvent(userState)
            if (response is NovaApiClient.EventResult.Final) {
                handleResult(context, response)
            } else {
                // EventResult.NeedMore: no open conversation to resume it on - counts as quiet,
                // not a failure.
                AmbientCheckResult.QUIET
            }
        } catch (e: IOException) {
            // Same stance as every other network call here: a failed check is silently missed,
            // never surfaced as an error to the user - only recorded for the debug UI.
            AmbientCheckResult.FAILED
        }
        SignalRepository.recordAmbientCheck(AmbientCheckOutcome(System.currentTimeMillis(), result))
    }

    /**
     * Two independent things a turn can hand back, neither gated on the other: [speech] is this
     * instant's verdict (worth interrupting right now, or not), while `scheduledDeparture` is a
     * standing fact about the future worth acting on even when speech stayed empty - see
     * EventOut.scheduled_departure's doc comment.
     *
     * [AmbientNotifier.notify] can decline to actually show anything (POST_NOTIFICATIONS not
     * granted) even when the backend had something to say - BLOCKED names that case explicitly
     * rather than reporting SPOKE for a nudge nobody could have seen. Same reasoning extends to
     * the acceptance post below: with nothing shown, there is no delivery to treat as accepted.
     */
    private suspend fun handleResult(
        context: Context, result: NovaApiClient.EventResult.Final
    ): AmbientCheckResult {
        val speech = result.speech.trim()
        val wantsToSpeak = speech.isNotEmpty() && speech != "..."
        var delivered = false
        if (wantsToSpeak) {
            delivered = AmbientNotifier.notify(context, speech)

            // "Shown" is the ambient equivalent of a voice turn's TTS finishing without being
            // talked over (VoiceScreen.kt's speak()/postOutcome) - there is no barge-in signal
            // here, so delivery itself is the acceptance signal that moves gain.
            if (delivered) {
                result.episodeId?.let { episodeId ->
                    try {
                        NovaApiClient.postOutcome(episodeId, accepted = true)
                    } catch (e: IOException) {
                        // Fire-and-forget, same as every other outcome post.
                    }
                }
            }
        }

        result.scheduledDeparture?.let { DepartureAlarmScheduler.schedule(context, it) }
        return when {
            !wantsToSpeak -> AmbientCheckResult.QUIET
            delivered -> AmbientCheckResult.SPOKE
            else -> AmbientCheckResult.BLOCKED
        }
    }
}
