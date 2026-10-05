package com.example.novav2.state

import com.example.novav2.network.NovaApiClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DeviceCompassTest {
    private val now = 1_000_000_000L

    private fun departure(lat: Double? = -35.2777, lng: Double? = 149.1185, minutesUntilStart: Double? = 40.0) =
        NovaApiClient.ScheduledDeparture(
            destination = "Birch Bldg 35", mode = "walking", leaveInMinutes = 20.0,
            minutesUntilStart = minutesUntilStart, eventTitle = "COMP2100",
            destinationLatitude = lat, destinationLongitude = lng,
        )

    @Test
    fun bearing_cardinalDirections() {
        assertEquals(0.0, DeviceCompass.bearingDegrees(-35.0, 149.0, -34.9, 149.0), 0.01)
        assertEquals(180.0, DeviceCompass.bearingDegrees(-35.0, 149.0, -35.1, 149.0), 0.01)
        assertEquals(90.0, DeviceCompass.bearingDegrees(0.0, 149.0, 0.0, 149.1), 0.01)
        assertEquals(270.0, DeviceCompass.bearingDegrees(0.0, 149.0, 0.0, 148.9), 0.01)
    }

    @Test
    fun bearing_northEastIsBetween() {
        val b = DeviceCompass.bearingDegrees(-35.0, 149.0, -34.99, 149.01)
        assertTrue("was $b", b in 30.0..60.0)
    }

    @Test
    fun distance_oneHundredthOfADegreeOfLatitudeIsAboutOneKilometre() {
        assertEquals(1_112.0, DeviceCompass.distanceMeters(-35.0, 149.0, -35.01, 149.0), 5.0)
    }

    @Test
    fun target_lastsThirtyMinutesPastTheEventStart() {
        val target = DeviceCompass.Target.from(departure(), now)!!
        assertEquals(now + 40 * 60_000L + DeviceCompass.GRACE_AFTER_START_MILLIS, target.untilMillis)
    }

    @Test
    fun target_noCoordinatesMeansNoCompass() {
        assertNull(DeviceCompass.Target.from(departure(lat = null), now))
        assertNull(DeviceCompass.Target.from(departure(lng = null), now))
    }

    @Test
    fun target_noStartFallsBackToTheLeaveBy() {
        val target = DeviceCompass.Target.from(departure(minutesUntilStart = null), now)!!
        assertEquals(now + 20 * 60_000L + DeviceCompass.FALLBACK_AFTER_LEAVE_BY_MILLIS, target.untilMillis)
    }

    @Test
    fun sharedPlace_holdsAgainstALeaveByUntilItExpires() {
        val shared = DeviceCompass.Target(-35.0, 149.0, now + 60_000, manual = true)
        assertTrue(!shared.yieldsToLeaveBy(now))
        assertTrue(shared.yieldsToLeaveBy(now + 60_000))
        assertTrue(DeviceCompass.Target(-35.0, 149.0, now + 60_000).yieldsToLeaveBy(now))
    }

    @Test
    fun read_pointsAtTheDestination() {
        val target = DeviceCompass.Target(-35.0, 149.0, now + 60_000)
        val reading = DeviceCompass.read(target, Pair(-35.1, 149.0), now)
        assertEquals(0.0, (reading as DeviceCompass.Reading.Heading).degrees, 0.01)
    }

    @Test
    fun read_finishedOnArrivalOrExpiry_noFixWithoutLocation() {
        val target = DeviceCompass.Target(-35.0, 149.0, now + 60_000)
        // ~110 m away - inside ARRIVED_METERS
        assertEquals(DeviceCompass.Reading.Finished, DeviceCompass.read(target, Pair(-35.001, 149.0), now))
        assertEquals(DeviceCompass.Reading.Finished, DeviceCompass.read(target, Pair(-35.1, 149.0), now + 60_000))
        assertEquals(DeviceCompass.Reading.NoFix, DeviceCompass.read(target, null, now))
    }
}
