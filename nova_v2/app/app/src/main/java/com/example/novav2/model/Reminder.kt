package com.example.novav2.model

/**
 * A reminder's lifecycle. Stored as [wire] in Room and
 * sent as-is in UserState.reminders, so these strings are part of the backend contract
 * (schemas/user_state.py's ReminderInfo.status).
 *
 *   pending  --alarm, deliver-->  fired  --Done-->  done
 *   pending  --alarm, defer-->    deferred --alarm--> fired | deferred
 *   fired    --Snooze-->          snoozed  --alarm--> fired
 *   any active --delete-->        cancelled (Undo restores the previous status)
 */
enum class ReminderStatus(val wire: String) {
    PENDING("pending"),
    SNOOZED("snoozed"),
    DEFERRED("deferred"),
    FIRED("fired"),
    DONE("done"),
    CANCELLED("cancelled");

    /** Waiting on an alarm - what [com.example.novav2.state.ReminderScheduler] schedules. */
    val isScheduled: Boolean get() = this == PENDING || this == SNOOZED || this == DEFERRED

    /** Not finished with - scheduled, or went off and hasn't been answered. */
    val isActive: Boolean get() = isScheduled || this == FIRED

    companion object {
        fun fromWire(value: String?): ReminderStatus =
            entries.firstOrNull { it.wire == value } ?: PENDING
    }
}

enum class ReminderPriority(val wire: String) {
    NORMAL("normal"),
    IMPORTANT("important");

    companion object {
        fun fromWire(value: String?): ReminderPriority =
            entries.firstOrNull { it.wire == value } ?: NORMAL
    }
}

/** Where a reminder came from. [INFERRED] is Nova acting on a stated obligation without being
 * asked (the Action's trigger was "inferred") - shown as "Suggested by Nova", and the only kind
 * whose Done/delete is reported back as a gain Outcome. */
enum class ReminderOrigin(val wire: String) {
    REQUESTED("requested"),
    INFERRED("inferred"),
    MANUAL("manual"),
    SYSTEM("system");

    companion object {
        fun fromWire(value: String?): ReminderOrigin =
            entries.firstOrNull { it.wire == value } ?: REQUESTED
    }
}

/** How a reminder repeats - the same plain fields add_calendar_event's recurrence uses
 * (calendar_tool.py's _RECURRENCE_SCHEMA), kept structured rather than as an RRULE because
 * the phone computes each next occurrence itself, in local time. */
data class ReminderRecurrence(
    val frequency: String,          // daily / weekly / monthly / yearly
    val interval: Int = 1,
    val count: Int? = null,         // occurrences in total, including the first
    val untilLocal: String? = null, // last possible occurrence, local wall clock
) {
    /** "weekly", "every 2 weeks" - for the UI and the backend window. */
    fun describe(): String {
        if (interval <= 1) return frequency
        val unit = when (frequency) {
            "daily" -> "days"
            "weekly" -> "weeks"
            "monthly" -> "months"
            else -> "years"
        }
        return "every $interval $unit"
    }

    companion object {
        val FREQUENCIES = listOf("daily", "weekly", "monthly", "yearly")
    }
}

/**
 * One reminder as the backend sees it on a voice turn - mirrors schemas/user_state.py's
 * ReminderInfo. Built by [com.example.novav2.state.ReminderRepository.serverWindow].
 */
data class ReminderSummary(
    val id: String,
    val text: String,
    val dueLocal: String,
    val minutesUntilDue: Int,
    val status: String,
    val priority: String,
    val firedMinutesAgo: Int?,
    val recurrence: String?,
)
