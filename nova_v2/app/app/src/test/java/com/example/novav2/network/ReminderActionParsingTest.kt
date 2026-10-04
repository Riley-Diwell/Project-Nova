package com.example.novav2.network

import com.example.novav2.model.ReminderSummary
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The set_reminder / update_reminder half of the /event response. */
class ReminderActionParsingTest {

    private fun actions(vararg json: String) = JSONArray("[${json.joinToString(",")}]")

    @Test
    fun parsesASetReminderWithItsTrigger() {
        val parsed = parseReminderActions(actions(
            """{"tool":"set_reminder","trigger":"inferred","ran":true,"reason":"diverged",
               "input":{"text":"Submit the form","due_local":"2026-09-23T17:00:00","priority":"important",
                        "utc_offset_minutes":600}}"""
        ))
        assertEquals(1, parsed.size)
        with(parsed[0]) {
            assertEquals("Submit the form", text)
            assertEquals("2026-09-23T17:00:00", dueLocal)
            assertNull(inMinutes)
            assertEquals("important", priority)
            assertEquals("inferred", trigger)
        }
    }

    @Test
    fun ranFalseIsSkipped() {
        val parsed = parseReminderActions(actions(
            """{"tool":"set_reminder","trigger":"requested","ran":false,"input":{"text":"x","in_minutes":5}}"""
        ))
        assertTrue(parsed.isEmpty())
    }

    @Test
    fun missingFieldsAreSkippedNotCrashed() {
        val parsed = parseReminderActions(actions(
            """{"tool":"set_reminder","ran":true,"input":{"due_local":"2026-09-23T17:00:00"}}""",
            """{"tool":"set_reminder","ran":true,"input":{"text":"no time"}}""",
            """{"tool":"set_reminder","ran":true}""",
            """{"tool":"set_reminder","ran":true,"input":{"text":"ok","in_minutes":20}}""",
        ))
        assertEquals(listOf("ok"), parsed.map { it.text })
        assertEquals(20, parsed[0].inMinutes)
        assertEquals("requested", parsed[0].trigger)
        assertEquals("normal", parsed[0].priority)
    }

    @Test
    fun otherToolsAreIgnored() {
        val parsed = parseReminderActions(actions(
            """{"tool":"set_timer","ran":true,"input":{"duration_seconds":600}}"""
        ))
        assertTrue(parsed.isEmpty())
    }

    @Test
    fun parsesRecurrence() {
        val parsed = parseReminderActions(actions(
            """{"tool":"set_reminder","ran":true,"input":{"text":"Timesheet","due_local":"2026-09-28T09:00:00",
               "recurrence":{"frequency":"weekly","interval":2,"count":5}}}"""
        ))
        val r = parsed.single().recurrence!!
        assertEquals("weekly", r.frequency)
        assertEquals(2, r.interval)
        assertEquals(5, r.count)
    }

    @Test
    fun parsesEachUpdateAction() {
        val id = "5b0f3c1e-2d4a-4c8e-9f1a-0123456789ab"
        val parsed = parseUpdateReminderActions(actions(
            """{"tool":"update_reminder","ran":true,"input":{"reminder_id":"$id","action":"snooze","label":"x","in_minutes":60}}""",
            """{"tool":"update_reminder","ran":true,"input":{"reminder_id":"$id","action":"edit","label":"x","shift_minutes":-30}}""",
            """{"tool":"update_reminder","ran":true,"input":{"reminder_id":"$id","action":"delete","label":"x"}}""",
            """{"tool":"update_reminder","ran":true,"input":{"reminder_id":"$id","action":"archive","label":"x"}}""",
            """{"tool":"update_reminder","ran":false,"input":{"reminder_id":"$id","action":"complete","label":"x"}}""",
        ))
        assertEquals(listOf("snooze", "edit", "delete"), parsed.map { it.action })
        assertEquals(60, parsed[0].inMinutes)
        assertEquals(-30, parsed[1].shiftMinutes)
    }

    @Test
    fun summarySerialisesToTheBackendShape() {
        val json = ReminderSummary("id-1", "Email", "2026-09-23T16:30:00", 42, "pending", "normal", null, null).toJson()
        assertEquals("2026-09-23T16:30:00", json.getString("due_local"))
        assertEquals(42, json.getInt("minutes_until_due"))
        assertTrue(json.isNull("fired_minutes_ago"))
        assertEquals(setOf("id", "text", "due_local", "minutes_until_due", "status", "priority",
            "fired_minutes_ago", "recurrence", "place", "every_time", "place_after_local"), json.keys().asSequence().toSet())
        assertTrue(json.isNull("place"))
    }

    @Test
    fun recurrenceRejectsUnknownFrequency() {
        assertNull(parseRecurrence(JSONObject("""{"frequency":"hourly"}""")))
    }
}
