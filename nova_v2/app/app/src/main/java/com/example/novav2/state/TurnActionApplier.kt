package com.example.novav2.state

import android.content.Context
import android.util.Log
import com.example.novav2.model.ReminderOrigin
import com.example.novav2.model.ReminderPriority
import com.example.novav2.network.NovaApiClient
import com.example.novav2.network.ReminderAction
import com.example.novav2.network.UpdateReminderAction
import java.time.ZoneId

/**
 * The one place a finished turn's fire-and-forget Actions are carried out, and the one place a
 * paused turn's client-tool request is resolved on-device - shared by VoiceScreen.sendMessage and
 * AssistVoiceService.handleTranscript (the path the wearable's voice recordings use). They used to keep
 * two copies of this and drift: timers and alarms spoken into the wearable were silently dropped.
 *
 * Calendar writes stay with the callers - they may need a permission prompt, and a delete always
 * needs the user's Yes/No - which only VoiceScreen can show.
 */
object TurnActionApplier {
    private const val TAG = "TurnActionApplier"
    private const val MAX_HOPS = 3

    /** Answers NeedMore requests on-device until the turn is final (or [MAX_HOPS] runs out, so
     * a misbehaving backend can't loop forever). [onHop] lets the UI say what it is doing. */
    suspend fun resolveNeedMore(
        context: Context,
        first: NovaApiClient.EventResult,
        onHop: (NovaApiClient.EventResult.NeedMore) -> Unit = {},
    ): NovaApiClient.EventResult {
        var result = first
        var hops = 0
        while (result is NovaApiClient.EventResult.NeedMore && hops < MAX_HOPS) {
            val need: NovaApiClient.EventResult.NeedMore = result
            onHop(need)
            result = when (need.requestType) {
                "get_reminders" -> {
                    val zone = ZoneId.systemDefault()
                    NovaApiClient.postContinueReminders(
                        need.sessionId,
                        ReminderRepository.range(
                            context,
                            ReminderTime.parseLocal(need.fromIso, zone),
                            ReminderTime.parseLocal(need.toIso, zone),
                            need.includeDone,
                        ),
                    )
                }
                "get_calendar_range" -> {
                    val from = parseIsoToEpochMillis(need.fromIso)
                    val to = parseIsoToEpochMillis(need.toIso)
                    NovaApiClient.postContinueEvent(
                        need.sessionId, CalendarSignal.rangeSnapshot(context, from, to).orEmpty(),
                    )
                }
                else -> NovaApiClient.postContinueEvent(need.sessionId, emptyList())
            }
            hops++
        }
        return result
    }

    /** Timers, alarms, reminders and the departure alarm - everything that needs no UI. Each
     * Action is applied independently: one malformed (LLM-produced) Action must not stop the
     * rest of the turn. */
    suspend fun applyHeadless(context: Context, final: NovaApiClient.EventResult.Final) {
        // Where the Clock app can't be opened (see AlarmIntents.canOpenClockNow), a timer or
        // alarm becomes an important Nova reminder at the same moment instead: it still buzzes
        // the wearable and re-buzzes once, rather than being reported as set and never ringing.
        val clockReachable = runCatching { AlarmIntents.canOpenClockNow(context) }.getOrDefault(true)
        final.timerActions.forEach {
            if (clockReachable) runCatching { AlarmIntents.setTimer(context, it.durationSeconds, it.label) }
            else timerAsReminder(context, it.durationSeconds, it.label, final.episodeId)
        }
        final.alarmActions.forEach {
            if (clockReachable) runCatching { AlarmIntents.setAlarm(context, it.hour, it.minute, it.label) }
            else alarmAsReminder(context, it.hour, it.minute, it.label, final.episodeId)
        }
        final.reminderActions.forEach { applySet(context, it, final.episodeId) }
        final.updateReminderActions.forEach { applyUpdate(context, it) }
        // Asking once also sets up the leave-now alarm for later - the same mechanism
        // SignalMonitorService's ambient checks use.
        final.scheduledDeparture?.let { DepartureAlarmScheduler.schedule(context, it) }
    }

    private suspend fun timerAsReminder(context: Context, seconds: Int, label: String?, episodeId: String?) {
        try {
            val due = ReminderTime.toLocal(System.currentTimeMillis(), ZoneId.systemDefault())
                .plusSeconds(seconds.coerceAtLeast(1).toLong())
            ReminderRepository.create(
                context,
                text = label?.trim()?.takeIf { it.isNotEmpty() }?.let { "Timer: $it" } ?: "Timer done",
                due = due,
                priority = ReminderPriority.IMPORTANT,
                origin = ReminderOrigin.REQUESTED,
                sourceEpisodeId = episodeId,
            )
        } catch (e: Exception) {
            Log.w(TAG, "timer fallback skipped: $e")
        }
    }

    /** The next [hour]:[minute] - today if it's still ahead, otherwise tomorrow, as a Clock
     * alarm would ring. */
    private suspend fun alarmAsReminder(context: Context, hour: Int, minute: Int, label: String?, episodeId: String?) {
        try {
            val now = ReminderTime.toLocal(System.currentTimeMillis(), ZoneId.systemDefault())
            val today = now.toLocalDate().atTime(hour, minute)
            ReminderRepository.create(
                context,
                text = label?.trim()?.takeIf { it.isNotEmpty() }?.let { "Alarm: $it" } ?: "Alarm",
                due = if (today.isAfter(now)) today else today.plusDays(1),
                priority = ReminderPriority.IMPORTANT,
                origin = ReminderOrigin.REQUESTED,
                sourceEpisodeId = episodeId,
            )
        } catch (e: Exception) {
            Log.w(TAG, "alarm fallback skipped: $e")
        }
    }

    private suspend fun applySet(context: Context, action: ReminderAction, episodeId: String?) {
        try {
            val origin = if (action.trigger == "inferred") ReminderOrigin.INFERRED else ReminderOrigin.REQUESTED
            val zone = ZoneId.systemDefault()
            val due = action.dueLocal?.let { ReminderTime.parseLocal(it, zone) }
                ?: action.inMinutes?.let {
                    // Counted from receipt, as the tool tells the model - a few seconds of
                    // latency don't matter, and the model never does clock arithmetic.
                    ReminderTime.toLocal(System.currentTimeMillis(), zone).plusMinutes(it.toLong())
                }
            if (action.place != null) {
                ReminderRepository.createAtPlace(
                    context,
                    text = action.text,
                    place = action.place,
                    everyTime = action.everyTime,
                    priority = ReminderPriority.fromWire(action.priority),
                    origin = origin,
                    sourceEpisodeId = episodeId,
                    deadline = due,
                    afterLocal = action.afterLocal,
                )
                return
            }
            if (due == null) return
            ReminderRepository.create(
                context,
                text = action.text,
                due = due,
                priority = ReminderPriority.fromWire(action.priority),
                origin = origin,
                sourceEpisodeId = episodeId,
                recurrence = action.recurrence,
            )
        } catch (e: Exception) {
            Log.w(TAG, "set_reminder skipped: $e")
        }
    }

    private suspend fun applyUpdate(context: Context, action: UpdateReminderAction) {
        try {
            // An unknown id is a logged no-op inside the repository - the model was told never
            // to invent one, and if it did anyway there is nothing sensible to change.
            when (action.action) {
                "complete" -> ReminderRepository.complete(context, action.id)
                "snooze" -> ReminderRepository.snooze(context, action.id, action.inMinutes)
                "edit" -> ReminderRepository.editFromAction(
                    context, action.id, action.text, action.dueLocal,
                    action.inMinutes, action.shiftMinutes, action.recurrence,
                    action.place, action.everyTime,
                )
                "delete" -> ReminderRepository.cancel(context, action.id)?.let {
                    ReminderNotifier.notifyUndo(context, it)
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "update_reminder skipped: $e")
        }
    }
}
