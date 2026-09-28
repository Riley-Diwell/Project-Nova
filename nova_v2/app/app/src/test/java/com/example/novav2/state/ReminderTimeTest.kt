package com.example.novav2.state

import com.example.novav2.model.ReminderRecurrence
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.ZonedDateTime

/** Floating local times in Australia/Sydney, across the 2026-10-04 daylight-saving gap and
 * the April overlap. */
class ReminderTimeTest {
    private val sydney = ZoneId.of("Australia/Sydney")
    private val perth = ZoneId.of("Australia/Perth")

    private fun at(local: String, zone: ZoneId = sydney) =
        ZonedDateTime.ofInstant(java.time.Instant.ofEpochMilli(ReminderTime.toMillis(local, zone)!!), zone)

    @Test
    fun resolvesAnOrdinaryTime() {
        val z = at("2026-09-23T16:30:00")
        assertEquals(16, z.hour)
        assertEquals(30, z.minute)
        assertEquals("+10:00", z.offset.id)
    }

    @Test
    fun theSpringForwardGapMovesLaterByAnHour() {
        // 02:30 on 4 Oct 2026 doesn't exist in Sydney - it fires at 03:30.
        val z = at("2026-10-04T02:30:00")
        assertEquals(3, z.hour)
        assertEquals(30, z.minute)
        assertEquals("+11:00", z.offset.id)
    }

    @Test
    fun theAutumnOverlapTakesTheEarlierInstant() {
        // 02:30 on 4 Apr 2027 happens twice; the first (still daylight time) wins.
        val z = at("2027-04-04T02:30:00")
        assertEquals(2, z.hour)
        assertEquals("+11:00", z.offset.id)
    }

    @Test
    fun aTimeZoneChangeKeepsTheWallClock() {
        // "9am" set in Sydney is 9am in Perth after flying there - not 7am.
        assertEquals(9, at("2026-10-05T09:00:00", perth).hour)
        val sydneyMillis = ReminderTime.toMillis("2026-10-05T09:00:00", sydney)!!
        val perthMillis = ReminderTime.toMillis("2026-10-05T09:00:00", perth)!!
        assertEquals(3 * 60 * 60_000L, perthMillis - sydneyMillis)
    }

    @Test
    fun weeklyRecurrenceStaysAtNineAcrossDst() {
        val monday = LocalDateTime.parse("2026-09-28T09:00:00")
        val (next, _) = ReminderTime.nextOccurrence(
            monday, ReminderRecurrence("weekly"), LocalDateTime.parse("2026-09-28T09:05:00"),
        )!!
        assertEquals(LocalDateTime.parse("2026-10-05T09:00:00"), next)
        // Local 9:00 both weeks, even though the offset changed in between.
        assertEquals(9, at(ReminderTime.format(next)).hour)
        assertEquals(
            7 * 24 * 60 * 60_000L - 60 * 60_000L,
            ReminderTime.toMillis(next, sydney) - ReminderTime.toMillis(monday, sydney),
        )
    }

    @Test
    fun recurrenceSkipsOccurrencesAlreadyPast() {
        val (next, _) = ReminderTime.nextOccurrence(
            LocalDateTime.parse("2026-09-20T20:00:00"), ReminderRecurrence("daily"),
            LocalDateTime.parse("2026-09-23T21:00:00"),
        )!!
        assertEquals(LocalDateTime.parse("2026-09-24T20:00:00"), next)
    }

    @Test
    fun recurrenceHonoursCountAndUntil() {
        val start = LocalDateTime.parse("2026-09-23T09:00:00")
        val now = LocalDateTime.parse("2026-09-23T09:01:00")
        val (_, left) = ReminderTime.nextOccurrence(start, ReminderRecurrence("daily", count = 3), now)!!
        assertEquals(2, left)
        assertNull(ReminderTime.nextOccurrence(start, ReminderRecurrence("daily", count = 1), now))
        assertNull(
            ReminderTime.nextOccurrence(
                start, ReminderRecurrence("weekly", untilLocal = "2026-09-29T00:00:00"), now,
            )
        )
    }

    @Test
    fun intervalAndMonthly() {
        val start = LocalDateTime.parse("2026-01-31T09:00:00")
        val now = start.plusMinutes(1)
        assertEquals(
            LocalDateTime.parse("2026-02-14T09:00:00"),
            ReminderTime.nextOccurrence(start, ReminderRecurrence("weekly", interval = 2), now)!!.first,
        )
        // plusMonths clamps to the end of February rather than overflowing.
        assertEquals(
            LocalDateTime.parse("2026-02-28T09:00:00"),
            ReminderTime.nextOccurrence(start, ReminderRecurrence("monthly"), now)!!.first,
        )
    }

    @Test
    fun parsesOffsetFormsIntoLocal() {
        assertEquals(
            LocalDateTime.parse("2026-09-23T16:30:00"),
            ReminderTime.parseLocal("2026-09-23T06:30:00Z", sydney),
        )
        assertNull(ReminderTime.parseLocal("tomorrow at 9", sydney))
    }

    @Test
    fun clockFormat() {
        assertEquals("4:30pm", ReminderTime.clock(LocalDateTime.parse("2026-09-23T16:30:00")))
        assertEquals("12:05am", ReminderTime.clock(LocalDateTime.parse("2026-09-23T00:05:00")))
        assertEquals("12:00pm", ReminderTime.clock(LocalDateTime.parse("2026-09-23T12:00:00")))
    }
}
