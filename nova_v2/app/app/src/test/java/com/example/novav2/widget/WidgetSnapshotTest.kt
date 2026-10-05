package com.example.novav2.widget

import com.example.novav2.data.ReminderEntity
import com.example.novav2.model.CalendarEventInfo
import com.example.novav2.model.ReminderStatus
import com.example.novav2.state.SavedDeparture
import com.example.novav2.widget.WidgetSnapshot.Target
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import java.time.ZoneId

/** The widget's selection, ordering, wording and refresh-boundary rules. */
class WidgetSnapshotTest {
    private val zone = ZoneId.of("Australia/Sydney")
    private val minute = 60_000L
    private fun at(local: String) = LocalDateTime.parse(local).atZone(zone).toInstant().toEpochMilli()
    private val now = at("2026-10-03T10:00:00") // a Saturday

    private fun reminder(
        id: String,
        status: ReminderStatus,
        trigger: Long,
        firedAt: Long? = null,
        placeOn: String? = null,
        placeLabel: String? = null,
    ) = ReminderEntity(
        id = id, text = "reminder $id", dueLocal = "", triggerAtMillis = trigger, status = status.wire,
        origin = "manual", createdAtMillis = 0L, updatedAtMillis = 0L, firedAtMillis = firedAt,
        placeOn = placeOn, placeLabel = placeLabel,
    )

    private fun event(
        title: String,
        start: Long,
        end: Long,
        id: Long = 1L,
        allDay: Boolean = false,
        availability: String = "busy",
        selfStatus: String = "accepted",
    ) = CalendarEventInfo(
        title = title, startMillis = start, endMillis = end, location = null, availability = availability,
        isAllDay = allDay, selfStatus = selfStatus, minutesUntilStart = ((start - now) / minute).toInt(), eventId = id,
    )

    private val noDevice = WidgetDevice(paired = false, connected = false, batteryPercent = null)

    private fun build(
        reminders: List<ReminderEntity> = emptyList(),
        events: List<CalendarEventInfo>? = emptyList(),
        departure: SavedDeparture? = null,
        device: WidgetDevice = noDevice,
        nowMillis: Long = now,
    ) = WidgetSnapshotBuilder.build(reminders, events, departure, device, nowMillis, zone)

    @Test
    fun remindersFiredFirstThenByTriggerHeldLeftOutAndCapped() {
        val snapshot = build(
            reminders = listOf(
                reminder("later", ReminderStatus.PENDING, at("2026-10-03T16:30:00")),
                reminder("place", ReminderStatus.PENDING, ReminderEntity.NO_TRIGGER, placeOn = "arrive", placeLabel = "the shops"),
                reminder("held", ReminderStatus.DEFERRED, at("2026-10-03T11:01:00")),
                reminder("snoozed", ReminderStatus.SNOOZED, at("2026-10-03T10:15:00")),
                reminder("fired", ReminderStatus.FIRED, ReminderEntity.NO_TRIGGER, firedAt = now - 5 * minute),
            ),
        )
        assertEquals(listOf("fired", "snoozed", "later"), snapshot.reminders.map { it.id })
        assertEquals(listOf("now", "10:15am", "4:30pm"), snapshot.reminders.map { it.whenLabel })
        assertEquals(1, snapshot.heldCount)
    }

    @Test
    fun placeRemindersGoLastAndShowTheirPlace() {
        val snapshot = build(
            reminders = listOf(
                reminder("arrive", ReminderStatus.PENDING, ReminderEntity.NO_TRIGGER, placeOn = "arrive", placeLabel = "the shops"),
                reminder("leave", ReminderStatus.PENDING, ReminderEntity.NO_TRIGGER, placeOn = "leave", placeLabel = "work"),
                reminder("timed", ReminderStatus.PENDING, at("2026-10-06T09:00:00")),
            ),
        )
        assertEquals("timed", snapshot.reminders.first().id)
        assertEquals("Tue 9am", snapshot.reminders.first().whenLabel)
        assertEquals(setOf("at the shops", "leaving work"), snapshot.reminders.drop(1).map { it.whenLabel }.toSet())
    }

    @Test
    fun anOldFiredReminderShowsWhenItWentOffNotNow() {
        val snapshot = build(
            reminders = listOf(reminder("old", ReminderStatus.FIRED, ReminderEntity.NO_TRIGGER, firedAt = at("2026-10-03T08:00:00"))),
        )
        assertEquals("8am", snapshot.reminders.single().whenLabel)
    }

    @Test
    fun nextLinePriority() {
        val lecture = event("COMP2100", at("2026-10-03T09:00:00"), at("2026-10-03T11:00:00"), id = 1)
        val tutorial = event("COMP2100 Tutorial", at("2026-10-03T14:00:00"), at("2026-10-03T15:00:00"), id = 2)
        val departure = SavedDeparture(at("2026-10-03T13:35:00"), at("2026-10-03T14:00:00"), "COMP2100 Tutorial", "ANU")
        val fired = reminder("fired", ReminderStatus.FIRED, ReminderEntity.NO_TRIGGER, firedAt = now - minute)
        val pending = reminder("pending", ReminderStatus.PENDING, at("2026-10-03T16:30:00"))
        val events = listOf(lecture, tutorial)

        val withDeparture = build(listOf(fired, pending), events, departure).next
        assertEquals("Leave by 1:35pm", withDeparture.title)
        assertEquals("for COMP2100 Tutorial", withDeparture.detail)
        assertEquals(Target.EVENT, withDeparture.target)
        assertEquals(2L, withDeparture.eventId)

        assertEquals("reminder fired", build(listOf(fired, pending), events).next.title)
        assertEquals("now", build(listOf(fired, pending), events).next.detail)
        assertEquals("reminder pending", build(listOf(pending), events).next.title)
        assertEquals(Target.REMINDERS, build(listOf(pending), events).next.target)
        assertEquals("In COMP2100 until 11am", build(events = events).next.title)
        assertEquals("COMP2100 Tutorial", build(events = listOf(tutorial)).next.title)
        assertEquals("Nothing coming up", build().next.title)
        assertEquals("Nothing coming up", build(events = null).next.title)
    }

    @Test
    fun eventInProgressUsesCommittedEventsOnly() {
        val start = at("2026-10-03T09:00:00")
        val end = at("2026-10-03T11:00:00")
        val inClass = build(events = listOf(event("COMP2100", start, end)))
        assertEquals("In COMP2100 until 11am", inClass.nowNext?.title)
        assertEquals(Target.EVENT, inClass.nowNext?.target)

        // All-day, free and declined entries commit nobody to anything.
        assertNull(build(events = listOf(event("Holiday", start, end, allDay = true))).nowNext)
        assertNull(build(events = listOf(event("Lunch?", start, end, availability = "free"))).nowNext)
        assertNull(build(events = listOf(event("Skipped", start, end, selfStatus = "declined"))).nowNext)
    }

    @Test
    fun nextEventCarriesItsMatchingLeaveBy() {
        val tutorial = event("COMP2100 Tutorial", at("2026-10-03T14:00:00"), at("2026-10-03T15:00:00"))
        val departure = SavedDeparture(at("2026-10-03T13:35:00"), at("2026-10-03T14:00:30"), "COMP2100 Tutorial", "ANU")
        val row = build(events = listOf(tutorial), departure = departure).nowNext!!
        assertEquals("COMP2100 Tutorial", row.title)
        assertEquals("2–3pm · leave by 1:35pm", row.detail)

        // A leave-by for a different event isn't pinned onto this one.
        val other = departure.copy(eventStartMillis = at("2026-10-03T17:00:00"), eventTitle = "Dinner")
        assertEquals("2–3pm", build(events = listOf(tutorial), departure = other).nowNext!!.detail)
    }

    @Test
    fun timeRangesShareTheirSuffix() {
        val e = event("Seminar", at("2026-10-03T13:30:00"), at("2026-10-03T14:30:00"))
        assertEquals("1:30–2:30pm", build(events = listOf(e)).nowNext!!.detail)
        val crossing = event("Brunch", at("2026-10-03T11:00:00"), at("2026-10-03T12:00:00"))
        assertEquals("11am–12pm", build(events = listOf(crossing)).nowNext!!.detail)
    }

    @Test
    fun aLeaveByMoreThan15MinutesOldIsDropped() {
        val stillFresh = SavedDeparture(now - 14 * minute, null, null, "Coffee Club")
        assertEquals("Leave by 9:46am", build(departure = stillFresh).next.title)
        assertEquals("for Coffee Club", build(departure = stillFresh).next.detail)

        val stale = SavedDeparture(now - 16 * minute, null, null, "Coffee Club")
        assertEquals("Nothing coming up", build(departure = stale).next.title)

        val eventStarted = SavedDeparture(now - 5 * minute, now - minute, "COMP2100", null)
        assertEquals("Nothing coming up", build(departure = eventStarted).next.title)
    }

    @Test
    fun signedOutShowsOnlyTheSignInPrompt() {
        val snapshot = WidgetSnapshot.signedOut()
        assertFalse(snapshot.signedIn)
        assertEquals("Sign in to Nova", snapshot.next.title)
        assertNull(snapshot.next.detail)
        assertNull(snapshot.nowNext)
        assertTrue(snapshot.reminders.isEmpty())
        assertEquals(0, snapshot.heldCount)
        assertNull(snapshot.device)
        assertNull(snapshot.nextBoundaryMillis)
        assertEquals("", snapshot.dateLabel)
    }

    @Test
    fun boundaryIsLocalMidnightWithNothingElseComing() {
        // Midnight in Sydney (UTC+10 here), not in UTC - and the night before DST starts.
        assertEquals(at("2026-10-04T00:00:00"), build().nextBoundaryMillis)
    }

    @Test
    fun boundaryIsTheEarliestChange() {
        val lecture = event("COMP2100", at("2026-10-03T09:00:00"), at("2026-10-03T11:00:00"), id = 1)
        val tutorial = event("Tutorial", at("2026-10-03T14:00:00"), at("2026-10-03T15:00:00"), id = 2)
        val pending = reminder("p", ReminderStatus.PENDING, at("2026-10-03T10:40:00"))
        val fired = reminder("f", ReminderStatus.FIRED, ReminderEntity.NO_TRIGGER, firedAt = now - 50 * minute)
        val departure = SavedDeparture(at("2026-10-03T10:30:00"), at("2026-10-03T14:00:00"), "Tutorial", null)

        assertEquals(at("2026-10-03T11:00:00"), build(events = listOf(lecture, tutorial)).nextBoundaryMillis)
        assertEquals(at("2026-10-03T10:40:00"), build(listOf(pending), listOf(lecture)).nextBoundaryMillis)
        assertEquals(at("2026-10-03T10:30:00"), build(listOf(pending), listOf(lecture), departure).nextBoundaryMillis)
        // A fired reminder's "now" runs out an hour after it went off.
        assertEquals(now + 10 * minute, build(listOf(fired, pending)).nextBoundaryMillis)
        // A place reminder waiting on its place has no time to wake for.
        val place = reminder("x", ReminderStatus.PENDING, ReminderEntity.NO_TRIGGER, placeOn = "arrive", placeLabel = "home")
        assertEquals(at("2026-10-04T00:00:00"), build(listOf(place)).nextBoundaryMillis)
    }

    @Test
    fun dateAndTimeWording() {
        assertEquals("Sat 3 Oct", build().dateLabel)
        assertEquals("4:30pm", WidgetSnapshotBuilder.time(at("2026-10-03T16:30:00"), now, zone))
        assertEquals("12pm", WidgetSnapshotBuilder.time(at("2026-10-03T12:00:00"), now, zone))
        assertEquals("Tue 9am", WidgetSnapshotBuilder.time(at("2026-10-06T09:00:00"), now, zone))
        assertEquals("Fri 9:05am", WidgetSnapshotBuilder.time(at("2026-10-02T09:05:00"), now, zone))
        assertEquals("12 Oct", WidgetSnapshotBuilder.time(at("2026-10-12T09:00:00"), now, zone))
    }

    @Test
    fun deviceLine() {
        assertNull(build().device)
        assertEquals("Nova not connected", build(device = WidgetDevice(true, false, null)).device)
        assertEquals("Nova · 64%", build(device = WidgetDevice(true, true, 64)).device)
        assertEquals("Nova connected", build(device = WidgetDevice(true, true, null)).device)
    }

    @Test
    fun deviceUpdatesAreThrottledToRealChanges() {
        val shown = WidgetDevice(paired = true, connected = true, batteryPercent = 64)
        assertTrue(WidgetSnapshotBuilder.deviceChangeWorthUpdate(null, shown))
        assertFalse(WidgetSnapshotBuilder.deviceChangeWorthUpdate(shown, shown.copy(batteryPercent = 60)))
        assertTrue(WidgetSnapshotBuilder.deviceChangeWorthUpdate(shown, shown.copy(batteryPercent = 59)))
        assertTrue(WidgetSnapshotBuilder.deviceChangeWorthUpdate(shown, shown.copy(connected = false, batteryPercent = null)))
        assertTrue(WidgetSnapshotBuilder.deviceChangeWorthUpdate(shown.copy(batteryPercent = null), shown))
    }
}
