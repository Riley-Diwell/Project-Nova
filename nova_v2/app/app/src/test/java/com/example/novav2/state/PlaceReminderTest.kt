package com.example.novav2.state

import com.example.novav2.data.ReminderEntity
import com.example.novav2.model.GeoPoint
import com.example.novav2.model.PlaceEvent
import com.example.novav2.model.ReminderPlace
import com.example.novav2.model.ReminderRecurrence
import com.example.novav2.model.ReminderStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import java.time.ZoneId

/** Place reminders through the state machine, and the naming around their geofences. */
class PlaceReminderTest {
    private val zone = ZoneId.of("Australia/Sydney")
    private val minute = 60_000L
    private val now = ReminderTime.toMillis(LocalDateTime.parse("2026-09-23T16:00:00"), zone)

    private val shops = ReminderPlace(
        PlaceEvent.ARRIVE, "the shops",
        listOf(GeoPoint("Lyneham Shops", -35.2519, 149.1251, 150f), GeoPoint("Canberra Centre", -35.2802, 149.1332, 286f)),
    )
    private val home = ReminderPlace(PlaceEvent.ARRIVE, "home", listOf(GeoPoint("12 Smith St", -35.26, 149.14, 150f)))

    private fun atShops(everyTime: Boolean = false) =
        ReminderTransitions.createAtPlace("Buy milk", shops, everyTime, now, id = "r1")

    @Test
    fun createdAtAPlaceWaitsWithNoTime() {
        val r = atShops()
        assertEquals(ReminderStatus.PENDING, r.statusEnum)
        assertEquals("", r.dueLocal)
        assertEquals(ReminderEntity.NO_TRIGGER, r.triggerAtMillis)
        assertEquals(shops, r.place)
        assertTrue(r.isPlaceReminder)
    }

    @Test
    fun aOneOffIsDoneOnceDone() {
        val fired = ReminderTransitions.fire(atShops(), now, null, zone)
        assertEquals(ReminderStatus.FIRED, fired.statusEnum)
        assertEquals(ReminderEntity.NO_TRIGGER, fired.triggerAtMillis)
        assertEquals(ReminderStatus.DONE, ReminderTransitions.complete(fired, now + minute, zone).statusEnum)
    }

    @Test
    fun everyTimeGoesBackToWaitingWhenDone() {
        val fired = ReminderTransitions.fire(atShops(everyTime = true), now, null, zone)
        val done = ReminderTransitions.complete(fired, now + minute, zone)
        assertEquals(ReminderStatus.PENDING, done.statusEnum)
        assertEquals(ReminderEntity.NO_TRIGGER, done.triggerAtMillis)
        assertEquals(now + minute, done.completedAtMillis)
        assertNull(done.firedAtMillis)
    }

    @Test
    fun heldThenFiredWaitsOnItsPlaceAgain() {
        // Arrived mid-lecture: held until it ends, then delivered by the alarm.
        val held = ReminderTransitions.defer(atShops(everyTime = true), now + 50 * minute, "COMP2100 Lecture", now)
        assertEquals(now + 50 * minute, held.triggerAtMillis)
        val fired = ReminderTransitions.fire(held, now + 50 * minute, null, zone)
        assertEquals(ReminderEntity.NO_TRIGGER, fired.triggerAtMillis)
    }

    @Test
    fun snoozingAPlaceReminderGivesItATime() {
        val fired = ReminderTransitions.fire(atShops(), now, null, zone)
        val snoozed = ReminderTransitions.snooze(fired, 10, now)
        assertEquals(ReminderStatus.SNOOZED, snoozed.statusEnum)
        assertEquals(now + 10 * minute, snoozed.triggerAtMillis)
    }

    @Test
    fun rezoneLeavesAPlaceReminderAlone() {
        val r = atShops()
        assertEquals(r, ReminderTransitions.rezone(r, ZoneId.of("Europe/London"), now))
    }

    @Test
    fun givingItATimeMakesItATimedReminder() {
        val due = LocalDateTime.parse("2026-09-23T18:00:00")
        val edited = ReminderTransitions.edit(atShops(everyTime = true), now, zone, due = due)
        assertFalse(edited.isPlaceReminder)
        assertNull(edited.place)
        assertFalse(edited.everyTime)
        assertEquals("2026-09-23T18:00:00", edited.dueLocal)
        assertEquals(ReminderTime.toMillis(due, zone), edited.triggerAtMillis)
    }

    @Test
    fun editingOnlyTheTextKeepsThePlace() {
        val edited = ReminderTransitions.edit(atShops(), now, zone, text = "Buy milk and bread")
        assertEquals(shops, edited.place)
        assertEquals(ReminderEntity.NO_TRIGGER, edited.triggerAtMillis)
    }

    @Test
    fun movingATimedReminderToAPlace() {
        val timed = ReminderTransitions.create(
            "Water the plants", LocalDateTime.parse("2026-09-23T18:00:00"), zone, now,
            recurrence = ReminderRecurrence("daily"), id = "r2",
        )
        val moved = ReminderTransitions.moveToPlace(timed, home, now + minute)
        assertEquals(ReminderStatus.PENDING, moved.statusEnum)
        assertEquals("", moved.dueLocal)
        assertEquals(ReminderEntity.NO_TRIGGER, moved.triggerAtMillis)
        assertNull(moved.recurrence)
        assertEquals(home, moved.place)
    }

    @Test
    fun aDeadlineIsAnOrdinaryDueTime() {
        val deadline = LocalDateTime.parse("2026-09-23T17:00:00")
        val r = ReminderTransitions.createAtPlace("Buy milk", shops, false, now, deadline = deadline, zone = zone)
        assertEquals("2026-09-23T17:00:00", r.dueLocal)
        assertEquals(ReminderTime.toMillis(deadline, zone), r.triggerAtMillis)
        // The flight home re-resolves the deadline like any floating time...
        val london = ZoneId.of("Europe/London")
        assertEquals(ReminderTime.toMillis(deadline, london), ReminderTransitions.rezone(r, london, now).triggerAtMillis)
        // ...and arriving first means it waits on nothing more.
        assertEquals(ReminderEntity.NO_TRIGGER, ReminderTransitions.fire(r, now, null, zone).triggerAtMillis)
    }

    @Test
    fun afterLocalIsKeptUntilTheReminderMoves() {
        val r = ReminderTransitions.createAtPlace("Return the book", shops, false, now, afterLocal = "2026-09-24T00:00:00")
        assertEquals("2026-09-24T00:00:00", r.placeAfterLocal)
        assertEquals(ReminderEntity.NO_TRIGGER, r.triggerAtMillis)
        assertNull(ReminderTransitions.moveToPlace(r, home, now).placeAfterLocal)
        assertNull(ReminderTransitions.edit(r, now, zone, due = LocalDateTime.parse("2026-09-24T09:00:00")).placeAfterLocal)
    }

    @Test
    fun placeTitles() {
        assertEquals("At the shops (Canberra Centre)", ReminderEngine.placeTitle(atShops(), "Canberra Centre"))
        val atHome = ReminderTransitions.createAtPlace("Bins out", home, false, now)
        // One place: its own name adds nothing.
        assertEquals("At home", ReminderEngine.placeTitle(atHome, "12 Smith St"))
        val leaving = ReminderTransitions.createAtPlace("Grab the charger", home.copy(on = PlaceEvent.LEAVE, label = "work"), false, now)
        assertEquals("Leaving work", ReminderEngine.placeTitle(leaving, null))
    }

    @Test
    fun pointsRoundTripInTheServersShape() {
        val json = ReminderPlace.encodePoints(shops.points)
        assertTrue(json.contains("\"radius_m\""))
        assertEquals(shops.points, ReminderPlace.decodePoints(json))
        assertTrue(ReminderPlace.decodePoints("not json").isEmpty())
        assertTrue(ReminderPlace.decodePoints(null as String?).isEmpty())
    }

    @Test
    fun requestIdsNameTheReminderAndCircle() {
        val id = GeofenceRegistrar.requestId("r1", 1, PlaceEvent.ARRIVE, -35.2802, 149.1332, 286f)
        assertEquals("r1" to 1, GeofenceRegistrar.parseRequestId(id))
        // A moved circle is a different geofence.
        val moved = GeofenceRegistrar.requestId("r1", 1, PlaceEvent.ARRIVE, -35.2803, 149.1332, 286f)
        assertFalse(id == moved)
        assertNull(GeofenceRegistrar.parseRequestId("someone-elses-id"))
    }
}
