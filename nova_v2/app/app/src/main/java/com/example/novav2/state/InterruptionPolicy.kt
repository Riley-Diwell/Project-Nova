package com.example.novav2.state

import com.example.novav2.model.CalendarEventInfo
import com.example.novav2.model.ReminderPriority
import java.time.Instant
import java.time.ZoneId

/**
 * When and how to interrupt the user with something Nova has to say at a set time - a pure
 * function of the situation at that moment. Deliberately
 * not reminder-specific, so notification management can reuse it once it has an Android producer.
 *
 * The phone decides this, not the backend, because the moment that matters is the alarm going
 * off - often offline, always without a model call - and the text delivered then is fixed and
 * written in code.
 *
 * The first matching row wins:
 *
 *   #  situation                          normal                         important
 *   1  on a call                          defer 5 min                    buzz + silent notification
 *   2  in a committed event (busy, not    defer to its end + 1 min       buzz + silent notification
 *      declined, not all-day)
 *   3  quiet hours 23:00-05:59            buzz + silent notification     same
 *   4  DND / restrictive filter           buzz + silent notification     same
 *   5  hands busy (vehicle, bike, run)    buzz + heads-up (+ speak)      same
 *   6  default                            buzz + heads-up (+ speak)      same
 *
 * "Speak" is headphone TTS, only with a headset connected and the setting on - never the phone
 * speaker. A deferral is capped at 3 deferrals or 3 hours in total, and never lands in quiet
 * hours; past the cap it is delivered as row 4.
 */
object InterruptionPolicy {
    // Mirrors control/observer.py's QUIET_HOURS, HANDS_BUSY_ACTIVITIES and RESTRICTIVE_FILTERS -
    // keep in sync, so the phone and the backend agree on what "a bad moment" is.
    val QUIET_HOURS = setOf(23, 0, 1, 2, 3, 4, 5)
    val HANDS_BUSY_ACTIVITIES = setOf("in_vehicle", "driving", "on_bicycle", "cycling", "running", "walking_fast")
    val RESTRICTIVE_FILTERS = setOf("priority", "alarms", "none")

    const val CALL_DEFER_MILLIS = 5 * 60_000L
    const val EVENT_END_GRACE_MILLIS = 60_000L
    const val MAX_DEFERRALS = 3
    const val MAX_DEFER_SPAN_MILLIS = 3 * 60 * 60_000L

    enum class Rule { ON_CALL, IN_EVENT, QUIET_HOURS, DO_NOT_DISTURB, HANDS_BUSY, DEFAULT, DEFER_CAPPED }

    /** A committed calendar event happening right now. */
    data class HeldEvent(val title: String, val endMillis: Long)

    data class Context(
        val nowMillis: Long,
        val zone: ZoneId,
        val callState: String?,
        val dnd: Boolean,
        val interruptionFilter: String?,
        val activity: String?,
        val currentEvent: HeldEvent?,
        val headsetConnected: Boolean,
        val speakWithHeadphones: Boolean,
    ) {
        val localHour: Int get() = Instant.ofEpochMilli(nowMillis).atZone(zone).hour
    }

    data class Item(
        val priority: ReminderPriority,
        val deferCount: Int = 0,
        val firstDeferredAtMillis: Long? = null,
    )

    sealed class Plan {
        abstract val rule: Rule

        /** Always a wearable buzz (a no-op if nothing is connected). */
        data class Deliver(val headsUp: Boolean, val speak: Boolean, override val rule: Rule) : Plan()

        data class Defer(val untilMillis: Long, val reason: String, override val rule: Rule) : Plan()
    }

    fun plan(ctx: Context, item: Item): Plan {
        val important = item.priority == ReminderPriority.IMPORTANT

        if (ctx.callState == "offhook" || ctx.callState == "ringing") {
            return if (important) silent(Rule.ON_CALL)
            else deferOrCap(ctx, item, ctx.nowMillis + CALL_DEFER_MILLIS, "call", Rule.ON_CALL)
        }

        ctx.currentEvent?.let { event ->
            return if (important) silent(Rule.IN_EVENT)
            else deferOrCap(ctx, item, event.endMillis + EVENT_END_GRACE_MILLIS, event.title, Rule.IN_EVENT)
        }

        if (ctx.localHour in QUIET_HOURS) return silent(Rule.QUIET_HOURS)

        if (ctx.dnd || ctx.interruptionFilter in RESTRICTIVE_FILTERS) return silent(Rule.DO_NOT_DISTURB)

        val speak = ctx.headsetConnected && ctx.speakWithHeadphones
        if (ctx.activity in HANDS_BUSY_ACTIVITIES) return Plan.Deliver(headsUp = true, speak = speak, rule = Rule.HANDS_BUSY)

        return Plan.Deliver(headsUp = true, speak = speak, rule = Rule.DEFAULT)
    }

    /** The committed event happening at [nowMillis], if any: busy, not declined, timed (all-day
     * and free entries commit nobody to anything - don't rely on calendarCtx, which counts them).
     * If several overlap, the one that ends last, so a deferral waits for all of them. */
    fun committedEventNow(events: List<CalendarEventInfo>, nowMillis: Long): HeldEvent? =
        events
            .filter {
                it.availability == "busy" && it.selfStatus != "declined" && !it.isAllDay &&
                    it.startMillis <= nowMillis && nowMillis < it.endMillis
            }
            .maxByOrNull { it.endMillis }
            ?.let { HeldEvent(it.title, it.endMillis) }

    private fun silent(rule: Rule) = Plan.Deliver(headsUp = false, speak = false, rule = rule)

    private fun deferOrCap(ctx: Context, item: Item, until: Long, reason: String, rule: Rule): Plan {
        val firstDeferred = item.firstDeferredAtMillis ?: ctx.nowMillis
        val untilHour = Instant.ofEpochMilli(until).atZone(ctx.zone).hour
        val capped = item.deferCount >= MAX_DEFERRALS ||
            until - firstDeferred > MAX_DEFER_SPAN_MILLIS ||
            untilHour in QUIET_HOURS
        return if (capped) silent(Rule.DEFER_CAPPED) else Plan.Defer(until, reason, rule)
    }
}
