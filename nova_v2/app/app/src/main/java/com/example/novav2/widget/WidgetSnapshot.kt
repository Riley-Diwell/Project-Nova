package com.example.novav2.widget

import com.example.novav2.data.ReminderEntity
import com.example.novav2.model.CalendarEventInfo
import com.example.novav2.model.PlaceEvent
import com.example.novav2.model.ReminderStatus
import com.example.novav2.state.InterruptionPolicy
import com.example.novav2.state.SavedDeparture
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.TextStyle
import java.time.temporal.ChronoUnit
import java.util.Locale
import kotlin.math.abs

/**
 * Everything the home-screen widget shows, already worded. The widget answers one question -
 * "what's next for me?" - from data that is already on the phone, so every line here is fixed,
 * code-owned wording ([WidgetSnapshotBuilder]) and never model text, for the same reason
 * AmbientCheckRunner never shows model speech.
 *
 * Times are absolute ("4:30pm", "Tue 9am"), never "in 12 min": the widget then only has to
 * refresh at real boundaries ([nextBoundaryMillis]), not every minute.
 */
data class WidgetSnapshot(
    val signedIn: Boolean,
    /** "Sat 3 Oct". */
    val dateLabel: String,
    /** The one line the small size shows. */
    val next: Line,
    /** The medium/large Now/Next row: the committed event in progress, else the next one (with
     * its leave-by when the server worked one out). Null with no calendar access or nothing on. */
    val nowNext: Line?,
    /** Fired-and-unanswered first, then by when they go off. Held (deferred) ones are left out -
     * they're counted in [heldCount] instead, since they are being kept back on purpose. Up to
     * [LARGE_REMINDERS]; the medium size shows fewer. */
    val reminders: List<ReminderRow>,
    /** Reminders being held until the user is free ("held until after class"). */
    val heldCount: Int,
    /** "Nova · 64%" / "Nova not connected" - null with no device paired, which is not a problem
     * worth a line. */
    val device: String?,
    /** The next moment the widget's contents change on their own; null when signed out. */
    val nextBoundaryMillis: Long?,
) {
    enum class Target { REMINDERS, EVENT, APP }

    /** [title] and an optional quieter [detail]; tapping it opens [target] ([eventId] for EVENT). */
    data class Line(
        val title: String,
        val detail: String? = null,
        val target: Target = Target.APP,
        val eventId: Long? = null,
    )

    data class ReminderRow(val id: String, val text: String, val whenLabel: String)

    companion object {
        const val MEDIUM_REMINDERS = 2
        const val LARGE_REMINDERS = 3

        /** Nothing but the sign-in prompt - never the previous user's data. */
        fun signedOut(): WidgetSnapshot = WidgetSnapshot(
            signedIn = false,
            dateLabel = "",
            next = Line("Sign in to Nova"),
            nowNext = null,
            reminders = emptyList(),
            heldCount = 0,
            device = null,
            nextBoundaryMillis = null,
        )
    }
}

/** The paired device as far as the widget cares. */
data class WidgetDevice(val paired: Boolean, val connected: Boolean, val batteryPercent: Int?)

/**
 * Builds a [WidgetSnapshot] from plain inputs - no Context, the same split as
 * [InterruptionPolicy] and DeviceButtonPolicy - so the selection and ordering rules are tested on
 * the JVM (WidgetSnapshotTest). WidgetSnapshotLoader gathers the inputs.
 */
object WidgetSnapshotBuilder {
    /** A fired reminder shows "now" for this long after it went off, then the time it went off -
     * "now" on something from yesterday would be a lie. */
    const val FIRED_NOW_MILLIS = 60 * 60_000L

    /** How far apart a leave-by's event start and a calendar entry's can be and still be the
     * same event (the server's minutes are rounded). */
    private const val SAME_START_TOLERANCE_MILLIS = 2 * 60_000L

    /**
     * [events] is null without calendar permission (the event row is then skipped silently).
     * [reminders] is ReminderDao.active(): scheduled, or fired and unanswered.
     */
    fun build(
        reminders: List<ReminderEntity>,
        events: List<CalendarEventInfo>?,
        departure: SavedDeparture?,
        device: WidgetDevice,
        nowMillis: Long,
        zone: ZoneId,
    ): WidgetSnapshot {
        val active = reminders.filter { it.statusEnum.isActive }
        val fired = active.filter { it.statusEnum == ReminderStatus.FIRED }
            .sortedByDescending { it.firedAtMillis ?: 0L }
        val waiting = active.filter { it.statusEnum == ReminderStatus.PENDING || it.statusEnum == ReminderStatus.SNOOZED }
            .sortedBy { it.triggerAtMillis } // place reminders' NO_TRIGGER sorts last on its own
        val held = active.filter { it.statusEnum == ReminderStatus.DEFERRED }
        val rows = (fired + waiting).take(WidgetSnapshot.LARGE_REMINDERS).map { row(it, nowMillis, zone) }

        val liveDeparture = departure?.takeIf { it.isLive(nowMillis) }
        val timed = events.orEmpty().filter { !it.isAllDay && it.selfStatus != "declined" }
        val current = events?.let { InterruptionPolicy.committedEventNow(it, nowMillis) }
            ?.let { h -> timed.firstOrNull { it.title == h.title && it.endMillis == h.endMillis } }
        val upcoming = timed.filter { it.startMillis > nowMillis }.minByOrNull { it.startMillis }
        val departureEvent = liveDeparture?.let { d -> timed.firstOrNull { matches(d, it) } }

        val departureLine = liveDeparture?.let { d ->
            WidgetSnapshot.Line(
                title = "Leave by ${time(d.leaveByMillis, nowMillis, zone)}",
                detail = (d.eventTitle ?: d.destination)?.let { "for $it" },
                target = if (departureEvent != null) WidgetSnapshot.Target.EVENT else WidgetSnapshot.Target.APP,
                eventId = departureEvent?.eventId,
            )
        }
        val currentLine = current?.let {
            WidgetSnapshot.Line(
                title = "In ${it.title} until ${time(it.endMillis, nowMillis, zone)}",
                target = WidgetSnapshot.Target.EVENT,
                eventId = it.eventId,
            )
        }
        val upcomingLine = upcoming?.let { e ->
            val leaveBy = liveDeparture?.takeIf { matches(it, e) }
                ?.let { " · leave by ${time(it.leaveByMillis, nowMillis, zone)}" }.orEmpty()
            WidgetSnapshot.Line(
                title = e.title,
                detail = range(e, nowMillis, zone) + leaveBy,
                target = WidgetSnapshot.Target.EVENT,
                eventId = e.eventId,
            )
        }

        val next = departureLine
            ?: fired.firstOrNull()?.let { reminderLine(it, nowMillis, zone) }
            ?: waiting.firstOrNull()?.let { reminderLine(it, nowMillis, zone) }
            ?: currentLine
            ?: upcomingLine
            ?: WidgetSnapshot.Line("Nothing coming up")

        return WidgetSnapshot(
            signedIn = true,
            dateLabel = dateLabel(nowMillis, zone),
            next = next,
            nowNext = currentLine ?: upcomingLine ?: departureLine,
            reminders = rows,
            heldCount = held.size,
            device = deviceLabel(device),
            nextBoundaryMillis = nextBoundary(
                nowMillis, zone, active, current, upcoming, liveDeparture,
            ),
        )
    }

    /**
     * The earliest moment after [nowMillis] that the widget would show something different with
     * no input changing: the current event ending, the next one starting, the leave-by and its
     * expiry, the next reminder going off, a fired reminder's "now" running out, and midnight
     * (the date line, and "Tue 9am" becoming "9am").
     */
    fun nextBoundary(
        nowMillis: Long,
        zone: ZoneId,
        reminders: List<ReminderEntity>,
        current: CalendarEventInfo?,
        upcoming: CalendarEventInfo?,
        departure: SavedDeparture?,
    ): Long {
        val midnight = Instant.ofEpochMilli(nowMillis).atZone(zone).toLocalDate().plusDays(1)
            .atStartOfDay(zone).toInstant().toEpochMilli()
        val candidates = buildList {
            add(midnight)
            current?.let { add(it.endMillis) }
            upcoming?.let { add(it.startMillis) }
            departure?.let { add(it.leaveByMillis); add(it.expiresAtMillis()) }
            reminders.filter { it.statusEnum.isScheduled && it.triggerAtMillis != ReminderEntity.NO_TRIGGER }
                .forEach { add(it.triggerAtMillis) }
            reminders.filter { it.statusEnum == ReminderStatus.FIRED }
                .forEach { r -> r.firedAtMillis?.let { add(it + FIRED_NOW_MILLIS) } }
        }
        return candidates.filter { it > nowMillis }.min()
    }

    /** Whether a device change is worth redrawing for: connecting or disconnecting, the first or
     * last battery reading, or the battery moving 5% or more - the device reports every ~15 s. */
    fun deviceChangeWorthUpdate(shown: WidgetDevice?, now: WidgetDevice): Boolean {
        if (shown == null) return true
        if (shown.paired != now.paired || shown.connected != now.connected) return true
        val before = shown.batteryPercent
        val after = now.batteryPercent
        if ((before == null) != (after == null)) return true
        return before != null && after != null && abs(after - before) >= 5
    }

    private fun matches(departure: SavedDeparture, event: CalendarEventInfo): Boolean {
        val start = departure.eventStartMillis
        if (start != null && abs(start - event.startMillis) <= SAME_START_TOLERANCE_MILLIS) return true
        return start == null && departure.eventTitle != null && departure.eventTitle == event.title
    }

    private fun row(r: ReminderEntity, nowMillis: Long, zone: ZoneId) =
        WidgetSnapshot.ReminderRow(r.id, r.text, whenLabel(r, nowMillis, zone))

    private fun reminderLine(r: ReminderEntity, nowMillis: Long, zone: ZoneId) =
        WidgetSnapshot.Line(r.text, whenLabel(r, nowMillis, zone), WidgetSnapshot.Target.REMINDERS)

    private fun whenLabel(r: ReminderEntity, nowMillis: Long, zone: ZoneId): String {
        if (r.statusEnum == ReminderStatus.FIRED) {
            val firedAt = r.firedAtMillis ?: return "now"
            return if (nowMillis - firedAt < FIRED_NOW_MILLIS) "now" else time(firedAt, nowMillis, zone)
        }
        if (r.triggerAtMillis != ReminderEntity.NO_TRIGGER) return time(r.triggerAtMillis, nowMillis, zone)
        val label = r.placeLabel?.takeIf { it.isNotBlank() } ?: return "at a place"
        return if (PlaceEvent.fromWire(r.placeOn) == PlaceEvent.LEAVE) "leaving $label" else "at $label"
    }

    private fun deviceLabel(device: WidgetDevice): String? = when {
        !device.paired -> null
        !device.connected -> "Nova not connected"
        device.batteryPercent != null -> "Nova · ${device.batteryPercent}%"
        else -> "Nova connected"
    }

    // --- Wording of times. English and fixed, like every other code-owned string here. ---

    /** "4:30pm" today; "Tue 9am" within a week either side; "12 Oct" further out. */
    fun time(millis: Long, nowMillis: Long, zone: ZoneId): String {
        val at = Instant.ofEpochMilli(millis).atZone(zone)
        val days = ChronoUnit.DAYS.between(today(nowMillis, zone), at.toLocalDate())
        return when {
            days == 0L -> clock(at)
            abs(days) < 7 -> "${dayName(at)} ${clock(at)}"
            else -> "${at.dayOfMonth} ${monthName(at)}"
        }
    }

    /** "11am–12pm", or "9:30–10:30am" when both ends share the half of the day. */
    private fun range(e: CalendarEventInfo, nowMillis: Long, zone: ZoneId): String {
        val start = time(e.startMillis, nowMillis, zone)
        val end = clock(Instant.ofEpochMilli(e.endMillis).atZone(zone))
        val startClock = clock(Instant.ofEpochMilli(e.startMillis).atZone(zone))
        val sameHalf = startClock.takeLast(2) == end.takeLast(2) && start.endsWith(startClock)
        return if (sameHalf) start.dropLast(2) + "–" + end else "$start–$end"
    }

    private fun clock(at: ZonedDateTime): String {
        val hour = if (at.hour % 12 == 0) 12 else at.hour % 12
        val suffix = if (at.hour < 12) "am" else "pm"
        return if (at.minute == 0) "$hour$suffix" else "$hour:${String.format(Locale.ENGLISH, "%02d", at.minute)}$suffix"
    }

    private fun dateLabel(nowMillis: Long, zone: ZoneId): String {
        val at = Instant.ofEpochMilli(nowMillis).atZone(zone)
        return "${dayName(at)} ${at.dayOfMonth} ${monthName(at)}"
    }

    private fun today(nowMillis: Long, zone: ZoneId): LocalDate = Instant.ofEpochMilli(nowMillis).atZone(zone).toLocalDate()
    private fun dayName(at: ZonedDateTime) = at.dayOfWeek.getDisplayName(TextStyle.SHORT, Locale.ENGLISH)
    private fun monthName(at: ZonedDateTime) = at.month.getDisplayName(TextStyle.SHORT, Locale.ENGLISH)
}
