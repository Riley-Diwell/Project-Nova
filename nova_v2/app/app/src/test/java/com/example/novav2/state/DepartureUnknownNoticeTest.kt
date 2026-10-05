package com.example.novav2.state

import com.example.novav2.network.NovaApiClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DepartureUnknownNoticeTest {

    private val now = 1_000_000_000L

    private fun unknown(
        title: String? = "Dentist",
        destination: String? = "12 Smith St",
        minutes: Double? = 40.0,
        reason: String? = "maps unavailable",
    ) = NovaApiClient.DepartureUnknown(destination, title, minutes, reason)

    @Test
    fun eventIsNamedByTitleThenDestination() {
        assertEquals(DepartureUnknownNotice.Event("Dentist", now + 40 * 60_000L),
            DepartureUnknownNotice.eventOf(unknown(), now))
        assertEquals("12 Smith St", DepartureUnknownNotice.eventOf(unknown(title = null), now)?.subject)
    }

    @Test
    fun noEventWithoutACalendarAnchorOrAName() {
        assertNull(DepartureUnknownNotice.eventOf(unknown(minutes = null), now))
        assertNull(DepartureUnknownNotice.eventOf(unknown(title = null, destination = null), now))
    }

    @Test
    fun sameEventTenMinutesLaterIsNotNew() {
        val first = DepartureUnknownNotice.eventOf(unknown(minutes = 40.0), now)!!
        // The next ambient check: ten minutes on, a whole-minute countdown off by one.
        val later = DepartureUnknownNotice.eventOf(unknown(minutes = 31.0), now + 10 * 60_000L)!!
        assertFalse(DepartureUnknownNotice.isNew(later, first))
    }

    @Test
    fun differentEventIsNew() {
        val first = DepartureUnknownNotice.eventOf(unknown(), now)!!
        assertTrue(DepartureUnknownNotice.isNew(first, null))
        assertTrue(DepartureUnknownNotice.isNew(first.copy(subject = "Gym"), first))
        // Same title, next week's occurrence.
        assertTrue(DepartureUnknownNotice.isNew(first.copy(startMillis = first.startMillis + 7 * 86_400_000L), first))
    }

    @Test
    fun noLocationSaysSo() {
        assertTrue(DepartureUnknownNotice.text("Dentist", "no location from the phone").contains("location"))
        assertEquals("Couldn't work out when to leave for Dentist. Check the route yourself.",
            DepartureUnknownNotice.text("Dentist", "maps unavailable"))
    }
}
