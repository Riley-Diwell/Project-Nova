package com.example.novav2.state

import com.example.novav2.model.ReminderOrigin
import com.example.novav2.model.ReminderPriority
import com.example.novav2.model.ReminderRecurrence
import com.example.novav2.model.ReminderStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalDateTime
import java.time.ZoneId

/** The reminder state machine, one transition at a time. */
class ReminderStateMachineTest {
    private val zone = ZoneId.of("Australia/Sydney")
    private val minute = 60_000L
    private val due = LocalDateTime.parse("2026-09-23T16:30:00")
    private val dueMillis = ReminderTime.toMillis(due, zone)
    private val created = dueMillis - 60 * minute

    private fun fresh(recurrence: ReminderRecurrence? = null) = ReminderTransitions.create(
        "Email Dr Chen", due, zone, created, ReminderPriority.NORMAL, ReminderOrigin.REQUESTED,
        "episode-1", recurrence, id = "r1",
    )

    @Test
    fun createIsPendingAtItsLocalTime() {
        val r = fresh()
        assertEquals(ReminderStatus.PENDING, r.statusEnum)
        assertEquals("2026-09-23T16:30:00", r.dueLocal)
        assertEquals(dueMillis, r.triggerAtMillis)
    }

    @Test
    fun fireThenDone() {
        val fired = ReminderTransitions.fire(fresh(), dueMillis, null)
        assertEquals(ReminderStatus.FIRED, fired.statusEnum)
        assertEquals(dueMillis, fired.firedAtMillis)
        val done = ReminderTransitions.complete(fired, dueMillis + minute, zone)
        assertEquals(ReminderStatus.DONE, done.statusEnum)
        assertEquals(dueMillis + minute, done.completedAtMillis)
    }

    @Test
    fun deferAccumulatesAndKeepsTheFirstInstant() {
        val once = ReminderTransitions.defer(fresh(), dueMillis + 50 * minute, "COMP2100 Lecture", dueMillis)
        val twice = ReminderTransitions.defer(once, dueMillis + 100 * minute, "COMP2100 Lab", dueMillis + 50 * minute)
        assertEquals(ReminderStatus.DEFERRED, twice.statusEnum)
        assertEquals(2, twice.deferCount)
        assertEquals(dueMillis, twice.firstDeferredAtMillis)
        assertEquals(dueMillis + 100 * minute, twice.triggerAtMillis)
        assertEquals("COMP2100 Lab", twice.deferReason)
        // The wall-clock time the user asked for is untouched.
        assertEquals("2026-09-23T16:30:00", twice.dueLocal)
    }

    @Test
    fun snoozeFromFired() {
        val fired = ReminderTransitions.fire(fresh(), dueMillis, dueMillis + 10 * minute)
        val snoozed = ReminderTransitions.snooze(fired, 10, dueMillis + minute)
        assertEquals(ReminderStatus.SNOOZED, snoozed.statusEnum)
        assertEquals(dueMillis + 11 * minute, snoozed.triggerAtMillis)
        assertEquals(1, snoozed.snoozeCount)
        assertNull(snoozed.rebuzzAtMillis)
    }

    @Test
    fun cancelAndUndoRestoreThePreviousStatus() {
        val fired = ReminderTransitions.fire(fresh(), dueMillis, null)
        val cancelled = ReminderTransitions.cancel(fired, dueMillis + minute)
        assertEquals(ReminderStatus.CANCELLED, cancelled.statusEnum)
        val restored = ReminderTransitions.undoCancel(cancelled, dueMillis + 2 * minute)
        assertEquals(ReminderStatus.FIRED, restored.statusEnum)
        assertNull(restored.previousStatus)
        // Undo of something not cancelled changes nothing.
        assertEquals(fired, ReminderTransitions.undoCancel(fired, dueMillis))
    }

    @Test
    fun editMakesItPendingAgainAtTheNewTime() {
        val deferred = ReminderTransitions.defer(fresh(), dueMillis + 50 * minute, "Lecture", dueMillis)
        val newDue = LocalDateTime.parse("2026-09-24T09:00:00")
        val edited = ReminderTransitions.edit(deferred, dueMillis, zone, text = "Email Dr Chen and Sam", due = newDue)
        assertEquals(ReminderStatus.PENDING, edited.statusEnum)
        assertEquals("2026-09-24T09:00:00", edited.dueLocal)
        assertEquals(ReminderTime.toMillis(newDue, zone), edited.triggerAtMillis)
        assertEquals(0, edited.deferCount)
        assertNull(edited.deferReason)
        assertEquals("Email Dr Chen and Sam", edited.text)
    }

    @Test
    fun editTextOnlyLeavesStatusAlone() {
        val fired = ReminderTransitions.fire(fresh(), dueMillis, null)
        val edited = ReminderTransitions.edit(fired, dueMillis + minute, zone, text = "New words")
        assertEquals(ReminderStatus.FIRED, edited.statusEnum)
        assertEquals("New words", edited.text)
    }

    @Test
    fun resolveEditDueAllThreeWays() {
        val r = fresh()
        val now = dueMillis - 30 * minute
        assertEquals(
            LocalDateTime.parse("2026-09-24T09:00:00"),
            ReminderTransitions.resolveEditDue(r, zone, now, "2026-09-24T09:00:00", null, null),
        )
        assertEquals(
            ReminderTime.toLocal(now, zone).plusMinutes(45),
            ReminderTransitions.resolveEditDue(r, zone, now, null, 45, null),
        )
        assertEquals(
            LocalDateTime.parse("2026-09-23T17:00:00"),
            ReminderTransitions.resolveEditDue(r, zone, now, null, null, 30),
        )
    }

    @Test
    fun completingARecurringReminderMovesToTheNextOccurrence() {
        val weekly = fresh(ReminderRecurrence("weekly", count = 3))
        val fired = ReminderTransitions.fire(weekly, dueMillis, null)
        val next = ReminderTransitions.complete(fired, dueMillis + minute, zone)
        assertEquals(ReminderStatus.PENDING, next.statusEnum)
        assertEquals("2026-09-30T16:30:00", next.dueLocal)
        assertEquals(2, next.recurCountRemaining)
        assertNull(next.firedAtMillis)
        assertEquals("r1", next.id)
    }

    @Test
    fun theLastOccurrenceIsDone() {
        val last = fresh(ReminderRecurrence("weekly", count = 1))
        assertEquals(ReminderStatus.DONE, ReminderTransitions.complete(last, dueMillis, zone).statusEnum)
    }

    @Test
    fun onlyADoneReminderCanBeCleared() {
        val done = ReminderTransitions.complete(ReminderTransitions.fire(fresh(), dueMillis, null), dueMillis, zone)
        val cleared = ReminderTransitions.clear(done, dueMillis + minute)!!
        assertEquals(dueMillis + minute, cleared.clearedAtMillis)
        assertEquals(ReminderStatus.DONE, cleared.statusEnum)
        assertEquals(dueMillis + minute, cleared.updatedAtMillis)
        assertNull(ReminderTransitions.clear(cleared, dueMillis + 2 * minute))
        assertNull(ReminderTransitions.clear(fresh(), dueMillis))
        assertNull(ReminderTransitions.unclear(cleared, dueMillis + 2 * minute)!!.clearedAtMillis)
    }

    @Test
    fun reArmingAClearedReminderUnclearsIt() {
        val done = ReminderTransitions.complete(fresh(), dueMillis, zone)
        val cleared = ReminderTransitions.clear(done, dueMillis)!!
        val edited = ReminderTransitions.edit(cleared, dueMillis, zone, due = LocalDateTime.parse("2026-09-24T09:00:00"))
        assertEquals(ReminderStatus.PENDING, edited.statusEnum)
        assertNull(edited.clearedAtMillis)
    }

    @Test
    fun firingARecurringReminderArmsTheNextOccurrence() {
        val daily = fresh(ReminderRecurrence("daily"))
        val fired = ReminderTransitions.fire(daily, dueMillis, null, zone)
        assertEquals(ReminderStatus.FIRED, fired.statusEnum)
        // The list still shows the one that went off; the alarm waits for tomorrow's.
        assertEquals("2026-09-23T16:30:00", fired.dueLocal)
        assertEquals(ReminderTime.toMillis(due.plusDays(1), zone), fired.triggerAtMillis)
    }

    @Test
    fun anIgnoredRepeatDoesNotBlockTheNextOne() {
        val daily = fresh(ReminderRecurrence("daily", count = 5))
        val fired = ReminderTransitions.fire(daily, dueMillis, null, zone)
        // Nothing to roll forward to until tomorrow's time arrives.
        assertNull(ReminderTransitions.rollForward(fired, dueMillis + 60 * minute, zone))
        val tomorrow = ReminderTime.toMillis(due.plusDays(1), zone)
        val next = ReminderTransitions.rollForward(fired, tomorrow, zone)!!
        assertEquals(ReminderStatus.PENDING, next.statusEnum)
        assertEquals("2026-09-24T16:30:00", next.dueLocal)
        assertEquals(4, next.recurCountRemaining)
        assertNull(next.firedAtMillis)
    }

    @Test
    fun severalMissedRepeatsArriveAsTheLatestOne() {
        val daily = fresh(ReminderRecurrence("daily"))
        // Phone off for three days, back on at 5pm on the 26th.
        val back = ReminderTime.toMillis(LocalDateTime.parse("2026-09-26T17:00:00"), zone)
        val rolled = ReminderTransitions.rollForward(daily, back, zone)!!
        assertEquals("2026-09-26T16:30:00", rolled.dueLocal)
    }

    @Test
    fun rollForwardStopsAtTheEndOfTheSeries() {
        val twice = fresh(ReminderRecurrence("daily", count = 2))
        val muchLater = ReminderTime.toMillis(LocalDateTime.parse("2026-09-30T09:00:00"), zone)
        val rolled = ReminderTransitions.rollForward(twice, muchLater, zone)!!
        assertEquals("2026-09-24T16:30:00", rolled.dueLocal)
        assertEquals(1, rolled.recurCountRemaining)
        // The last occurrence fires as an ordinary reminder - nothing further to arm.
        val fired = ReminderTransitions.fire(rolled, muchLater, null, zone)
        assertNull(fired.recurFrequency)
        assertNull(ReminderTransitions.rollForward(fired, muchLater, zone))
    }

    @Test
    fun aDifferentTimeOfDayStillFiresAcrossTheDstGap() {
        // 02:30 doesn't exist on 4 Oct in Sydney - that occurrence resolves to 03:30.
        val early = ReminderTransitions.create(
            "Meds", LocalDateTime.parse("2026-10-03T02:30:00"), zone, created, recurrence = ReminderRecurrence("daily"),
        )
        val fired = ReminderTransitions.fire(early, early.triggerAtMillis, null, zone)
        assertEquals(ReminderTime.toMillis(LocalDateTime.parse("2026-10-04T03:30:00"), zone), fired.triggerAtMillis)
    }

    @Test
    fun rezoneOnlyMovesPendingFloatingTimes() {
        val perth = ZoneId.of("Australia/Perth")
        val pending = fresh()
        assertEquals(ReminderTime.toMillis(due, perth), ReminderTransitions.rezone(pending, perth).triggerAtMillis)
        val snoozed = ReminderTransitions.snooze(ReminderTransitions.fire(pending, dueMillis, null), 10, dueMillis)
        assertEquals(snoozed, ReminderTransitions.rezone(snoozed, perth))
    }
}
