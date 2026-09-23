package com.example.novav2.state

import android.content.Context
import com.example.novav2.model.UserState
import com.example.novav2.network.NovaApiClient
import java.io.IOException
import kotlin.math.roundToLong

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

    // How close leave_in_minutes has to be before it's worth a heads-up rather than just
    // (re)scheduling the precise alarm for later - see leaveSoonText. Picked to match "you should
    // start thinking about this now", not "this is already urgent" (DepartureAlarmReceiver's own
    // alarm covers the actual leave-by moment).
    private const val IMMINENT_LEAVE_MINUTES = 15.0

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
     * Deliberately ignores [NovaApiClient.EventResult.Final.speech] - an ambient turn is never
     * supposed to produce any (see intent_surface.py's SYSTEM_PROMPT), and trusting free-form
     * model text as notification copy is exactly what let a turn that ignored that instruction
     * narrate itself onto the user's phone instead of staying quiet. The one thing worth an
     * ambient notification - a commitment coming up soon enough to leave for - is deterministic,
     * so it gets a fixed, code-owned sentence built from the tool's own numbers instead: see
     * [leaveSoonText].
     *
     * `scheduledDeparture` is scheduled unconditionally, independent of whether it was imminent
     * enough to also notify on right now - see EventOut.scheduled_departure's doc comment.
     *
     * [AmbientNotifier.notify] can decline to actually show anything (POST_NOTIFICATIONS not
     * granted) even when there was something to say - BLOCKED names that case explicitly rather
     * than reporting SPOKE for a nudge nobody could have seen. Same reasoning extends to the
     * acceptance post below: with nothing shown, there is no delivery to treat as accepted.
     */
    private suspend fun handleResult(
        context: Context, result: NovaApiClient.EventResult.Final
    ): AmbientCheckResult {
        val departure = result.scheduledDeparture
        departure?.let { DepartureAlarmScheduler.schedule(context, it) }

        val text = departure?.let { leaveSoonText(it) } ?: return AmbientCheckResult.QUIET
        val delivered = AmbientNotifier.notify(context, text, ledFlash = AmbientNotifier.LedFlash.SLOW)

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
        return if (delivered) AmbientCheckResult.SPOKE else AmbientCheckResult.BLOCKED
    }

    /**
     * The one and only phrasing an ambient "leave soon" nudge ever uses - fixed here, not
     * model-generated, so nothing the LLM says can reach the user as notification text (see
     * [handleResult]). Null when there is nothing worth surfacing yet: no countdown at all, one
     * with no calendar anchor to say "in N minutes" about, or one further out than
     * [IMMINENT_LEAVE_MINUTES] - DepartureAlarmScheduler still gets it regardless, for the later
     * precise alarm.
     *
     * Names the trip by [ScheduledDeparture.eventTitle] ("your dentist appointment") when the
     * server had one - copied verbatim from the calendar entry, never model-phrased - and falls
     * back to [ScheduledDeparture.destination] ("Coffee Club") for the rare case a calendar
     * anchor exists without a title.
     */
    private fun leaveSoonText(departure: NovaApiClient.ScheduledDeparture): String? {
        val subject = departure.eventTitle ?: departure.destination ?: return null
        val minutesUntilStart = departure.minutesUntilStart ?: return null
        if (departure.leaveInMinutes > IMMINENT_LEAVE_MINUTES) return null

        val leaveIn = departure.leaveInMinutes.coerceAtLeast(0.0).roundToLong()
        val eventIn = minutesUntilStart.coerceAtLeast(0.0).roundToLong()
        return "You have $subject in $eventIn minutes, you should leave in $leaveIn " +
            "minutes to be on-time."
    }
}
