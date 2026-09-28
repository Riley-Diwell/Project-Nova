package com.example.novav2.state

import com.example.novav2.model.CalendarEventInfo
import com.example.novav2.model.ReminderPriority
import com.example.novav2.state.InterruptionPolicy.Plan
import com.example.novav2.state.InterruptionPolicy.Rule
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import java.time.ZoneId

/** Every row of the policy table, both priorities, the deferral caps and the all-day exclusion. */
class InterruptionPolicyTest {
    private val zone = ZoneId.of("Australia/Sydney")
    private val now = LocalDateTime.parse("2026-09-23T10:00:00").atZone(zone).toInstant().toEpochMilli()
    private val minute = 60_000L

    private fun ctx(
        callState: String? = "idle",
        dnd: Boolean = false,
        filter: String? = "all",
        activity: String? = "still",
        event: InterruptionPolicy.HeldEvent? = null,
        headset: Boolean = false,
        speak: Boolean = false,
        nowMillis: Long = now,
    ) = InterruptionPolicy.Context(nowMillis, zone, callState, dnd, filter, activity, event, headset, speak)

    private val normal = InterruptionPolicy.Item(ReminderPriority.NORMAL)
    private val important = InterruptionPolicy.Item(ReminderPriority.IMPORTANT)
    private val lecture = InterruptionPolicy.HeldEvent("COMP2100 Lecture", now + 50 * minute)

    private fun deliver(plan: Plan) = plan as Plan.Deliver
    private fun defer(plan: Plan) = plan as Plan.Defer

    @Test
    fun row1_onACall() {
        for (state in listOf("offhook", "ringing")) {
            val held = defer(InterruptionPolicy.plan(ctx(callState = state), normal))
            assertEquals(now + 5 * minute, held.untilMillis)
            assertEquals("call", held.reason)
            val buzz = deliver(InterruptionPolicy.plan(ctx(callState = state), important))
            assertEquals(false, buzz.headsUp)
            assertEquals(Rule.ON_CALL, buzz.rule)
        }
    }

    @Test
    fun row2_inACommittedEvent() {
        val held = defer(InterruptionPolicy.plan(ctx(event = lecture), normal))
        assertEquals(lecture.endMillis + minute, held.untilMillis)
        assertEquals("COMP2100 Lecture", held.reason)
        val tap = deliver(InterruptionPolicy.plan(ctx(event = lecture), important))
        assertEquals(false, tap.headsUp)
        assertEquals(Rule.IN_EVENT, tap.rule)
    }

    @Test
    fun row3_quietHours() {
        val late = LocalDateTime.parse("2026-09-23T23:30:00").atZone(zone).toInstant().toEpochMilli()
        for (item in listOf(normal, important)) {
            val plan = deliver(InterruptionPolicy.plan(ctx(nowMillis = late), item))
            assertEquals(Rule.QUIET_HOURS, plan.rule)
            assertEquals(false, plan.headsUp)
        }
    }

    @Test
    fun row4_doNotDisturb() {
        for (c in listOf(ctx(dnd = true), ctx(filter = "priority"), ctx(filter = "alarms"), ctx(filter = "none"))) {
            val plan = deliver(InterruptionPolicy.plan(c, normal))
            assertEquals(Rule.DO_NOT_DISTURB, plan.rule)
            assertEquals(false, plan.headsUp)
        }
    }

    @Test
    fun row5_handsBusy() {
        val plan = deliver(InterruptionPolicy.plan(ctx(activity = "in_vehicle", headset = true, speak = true), normal))
        assertEquals(Rule.HANDS_BUSY, plan.rule)
        assertTrue(plan.headsUp)
        assertTrue(plan.speak)
    }

    @Test
    fun row6_default() {
        val plan = deliver(InterruptionPolicy.plan(ctx(), normal))
        assertEquals(Rule.DEFAULT, plan.rule)
        assertTrue(plan.headsUp)
        assertEquals(false, plan.speak)
    }

    @Test
    fun speaksOnlyWithAHeadsetAndTheSettingOn() {
        assertEquals(false, deliver(InterruptionPolicy.plan(ctx(headset = true, speak = false), normal)).speak)
        assertEquals(false, deliver(InterruptionPolicy.plan(ctx(headset = false, speak = true), normal)).speak)
        assertEquals(true, deliver(InterruptionPolicy.plan(ctx(headset = true, speak = true), normal)).speak)
        // Never in class or on DND, headset or not.
        assertEquals(false, deliver(InterruptionPolicy.plan(ctx(dnd = true, headset = true, speak = true), normal)).speak)
    }

    @Test
    fun theFirstMatchingRowWins() {
        // On a call during a lecture with DND on: the call row decides.
        val plan = InterruptionPolicy.plan(ctx(callState = "offhook", event = lecture, dnd = true), normal)
        assertEquals(Rule.ON_CALL, plan.rule)
    }

    @Test
    fun deferralIsCappedByCount() {
        val third = InterruptionPolicy.Item(ReminderPriority.NORMAL, deferCount = 3, firstDeferredAtMillis = now - 30 * minute)
        val plan = deliver(InterruptionPolicy.plan(ctx(event = lecture), third))
        assertEquals(Rule.DEFER_CAPPED, plan.rule)
        assertEquals(false, plan.headsUp)
    }

    @Test
    fun deferralIsCappedAtThreeHoursInTotal() {
        val longHeld = InterruptionPolicy.Item(ReminderPriority.NORMAL, deferCount = 1, firstDeferredAtMillis = now - 150 * minute)
        assertEquals(Rule.DEFER_CAPPED, InterruptionPolicy.plan(ctx(event = lecture), longHeld).rule)
        val exam = InterruptionPolicy.HeldEvent("Exam", now + 200 * minute)
        assertEquals(Rule.DEFER_CAPPED, InterruptionPolicy.plan(ctx(event = exam), normal).rule)
    }

    @Test
    fun neverDefersIntoQuietHours() {
        val evening = LocalDateTime.parse("2026-09-23T21:30:00").atZone(zone).toInstant().toEpochMilli()
        val lateClass = InterruptionPolicy.HeldEvent("Night lab", evening + 100 * minute) // ends 23:10
        val plan = InterruptionPolicy.plan(ctx(nowMillis = evening, event = lateClass), normal)
        assertEquals(Rule.DEFER_CAPPED, plan.rule)
    }

    private fun event(
        title: String, start: Long, end: Long,
        availability: String = "busy", allDay: Boolean = false, self: String = "accepted",
    ) = CalendarEventInfo(title, start, end, null, availability, allDay, self, ((start - now) / minute).toInt(), 1L)

    @Test
    fun committedEventExcludesAllDayFreeAndDeclined() {
        val allDay = event("Public holiday", now - 600 * minute, now + 600 * minute, allDay = true)
        val free = event("Office hours", now - 10 * minute, now + 20 * minute, availability = "free")
        val declined = event("Standup", now - 10 * minute, now + 20 * minute, self = "declined")
        assertNull(InterruptionPolicy.committedEventNow(listOf(allDay, free, declined), now))
    }

    @Test
    fun committedEventTakesTheOneThatEndsLast() {
        val a = event("Lecture", now - 10 * minute, now + 40 * minute)
        val b = event("Lab", now - 5 * minute, now + 90 * minute)
        val upcoming = event("Later", now + 30 * minute, now + 60 * minute)
        assertEquals("Lab", InterruptionPolicy.committedEventNow(listOf(a, b, upcoming), now)?.title)
    }
}
