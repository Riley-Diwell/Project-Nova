package com.example.novav2.state

import com.example.novav2.network.NovaApiClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** DepartureStore's pure half: relative countdowns turned into instants, and when they go stale. */
class SavedDepartureTest {
    private val now = 1_800_000_000_000L
    private val minute = 60_000L

    private fun departure(leaveIn: Double, untilStart: Double?, title: String? = "COMP2100") =
        NovaApiClient.ScheduledDeparture(
            destination = "ANU", mode = "driving", leaveInMinutes = leaveIn,
            minutesUntilStart = untilStart, eventTitle = title,
        )

    @Test
    fun countdownsBecomeInstantsAtArrival() {
        val saved = SavedDeparture.from(departure(25.0, 40.0), now)
        assertEquals(now + 25 * minute, saved.leaveByMillis)
        assertEquals(now + 40 * minute, saved.eventStartMillis)
        assertEquals("COMP2100", saved.eventTitle)
        assertEquals("ANU", saved.destination)
    }

    @Test
    fun alreadyLateLeavesByNow() {
        assertEquals(now, SavedDeparture.from(departure(-3.0, 5.0), now).leaveByMillis)
    }

    @Test
    fun noCalendarAnchorHasNoEventStart() {
        assertNull(SavedDeparture.from(departure(10.0, null, title = null), now).eventStartMillis)
    }

    @Test
    fun liveUntil15MinutesPastLeaveByOrTheEventStarting() {
        val anchored = SavedDeparture(now, now + 10 * minute, "COMP2100", "ANU")
        assertTrue(anchored.isLive(now + 9 * minute))
        assertFalse(anchored.isLive(now + 10 * minute))
        assertEquals(now + 10 * minute, anchored.expiresAtMillis())

        val unanchored = SavedDeparture(now, null, null, "Coffee Club")
        assertTrue(unanchored.isLive(now + 14 * minute))
        assertFalse(unanchored.isLive(now + 15 * minute))
        assertEquals(now + SavedDeparture.STALE_AFTER_MILLIS, unanchored.expiresAtMillis())
    }
}
