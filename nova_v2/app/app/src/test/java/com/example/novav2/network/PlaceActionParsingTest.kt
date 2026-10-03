package com.example.novav2.network

import com.example.novav2.model.PlaceEvent
import com.example.novav2.model.ReminderSummary
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Place reminders on the /event wire - the place the backend resolved (places.py) arriving in
 * set_reminder / update_reminder Actions, and going back out in the reminder window. */
class PlaceActionParsingTest {

    private fun actions(vararg json: String) = JSONArray("[${json.joinToString(",")}]")

    private val shops = """{"on":"arrive","match":"type","label":"the shops","points":[
        {"name":"Lyneham Shops","lat":-35.2519,"lng":149.1251,"radius_m":150},
        {"name":"Canberra Centre","lat":-35.2802,"lng":149.1332,"radius_m":286}]}"""

    @Test
    fun parsesASetReminderAtAPlace() {
        val parsed = parseReminderActions(actions(
            """{"tool":"set_reminder","trigger":"requested","ran":true,
               "input":{"text":"Buy milk","place":$shops,"every_time":true,"utc_offset_minutes":600}}"""
        ))
        with(parsed.single()) {
            assertEquals("Buy milk", text)
            assertNull(dueLocal)
            assertNull(inMinutes)
            assertTrue(everyTime)
            val place = place!!
            assertEquals(PlaceEvent.ARRIVE, place.on)
            assertEquals("the shops", place.label)
            assertEquals(listOf("Lyneham Shops", "Canberra Centre"), place.points.map { it.name })
            assertEquals(286f, place.points[1].radiusMeters)
        }
    }

    @Test
    fun aTimeWithAPlaceIsKeptAsItsDeadline() {
        val parsed = parseReminderActions(actions(
            """{"tool":"set_reminder","ran":true,"input":{"text":"Buy milk","place":$shops,
               "due_local":"2026-09-23T17:00:00","after_local":"2026-09-23T12:00:00"}}"""
        ))
        with(parsed.single()) {
            assertEquals("2026-09-23T17:00:00", dueLocal)
            assertEquals("2026-09-23T12:00:00", afterLocal)
            assertEquals("the shops", place?.label)
        }
    }

    @Test
    fun afterLocalIsIgnoredWithoutAPlace() {
        val parsed = parseReminderActions(actions(
            """{"tool":"set_reminder","ran":true,"input":{"text":"x","in_minutes":5,"after_local":"2026-09-23T12:00:00"}}"""
        ))
        assertNull(parsed.single().afterLocal)
    }

    @Test
    fun aPlaceWithNoUsableCirclesIsNotAReminder() {
        val parsed = parseReminderActions(actions(
            """{"tool":"set_reminder","ran":true,"input":{"text":"Buy milk",
               "place":{"on":"arrive","label":"the shops","points":[{"name":"x","lat":"north"}]}}}"""
        ))
        assertTrue(parsed.isEmpty())
    }

    @Test
    fun everyTimeIsIgnoredWithoutAPlace() {
        val parsed = parseReminderActions(actions(
            """{"tool":"set_reminder","ran":true,"input":{"text":"Stretch","in_minutes":5,"every_time":true}}"""
        ))
        assertFalse(parsed.single().everyTime)
        assertNull(parsed.single().place)
    }

    @Test
    fun anUnknownEventIsNoPlace() {
        assertNull(parsePlace(JSONObject("""{"on":"near","label":"x","points":[{"name":"x","lat":1,"lng":2,"radius_m":150}]}""")))
    }

    @Test
    fun parsesAnEditMovingAReminderToAPlace() {
        val parsed = parseUpdateReminderActions(actions(
            """{"tool":"update_reminder","ran":true,"input":{"reminder_id":"r1","action":"edit","label":"Buy milk",
               "place":{"on":"leave","label":"work","points":[{"name":"CSIT","lat":-35.275,"lng":149.12,"radius_m":150}]},
               "every_time":false}}"""
        ))
        with(parsed.single()) {
            assertEquals(PlaceEvent.LEAVE, place?.on)
            assertEquals("work", place?.label)
            assertEquals(false, everyTime)
        }
    }

    @Test
    fun anEditWithoutEveryTimeLeavesItAlone() {
        val parsed = parseUpdateReminderActions(actions(
            """{"tool":"update_reminder","ran":true,"input":{"reminder_id":"r1","action":"edit","label":"x","text":"y"}}"""
        ))
        assertNull(parsed.single().everyTime)
        assertNull(parsed.single().place)
    }

    @Test
    fun aPlaceSummaryHasNoTime() {
        val json = ReminderSummary("id-1", "Buy milk", null, null, "pending", "normal", null, null,
            place = "arrive: the shops", everyTime = true, placeAfterLocal = "2026-09-24T00:00:00").toJson()
        assertTrue(json.isNull("due_local"))
        assertTrue(json.isNull("minutes_until_due"))
        assertEquals("arrive: the shops", json.getString("place"))
        assertTrue(json.getBoolean("every_time"))
        assertEquals("2026-09-24T00:00:00", json.getString("place_after_local"))
    }
}
