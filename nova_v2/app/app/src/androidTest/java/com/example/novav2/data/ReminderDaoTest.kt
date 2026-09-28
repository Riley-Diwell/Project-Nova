package com.example.novav2.data

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.example.novav2.model.ReminderStatus
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/** The scheduling queries: nextTrigger across scheduled rows and re-buzzes, and what's due. */
@RunWith(AndroidJUnit4::class)
class ReminderDaoTest {
    private lateinit var db: NovaDatabase
    private lateinit var dao: ReminderDao

    @Before
    fun setUp() {
        db = Room.inMemoryDatabaseBuilder(
            ApplicationProvider.getApplicationContext(), NovaDatabase::class.java,
        ).build()
        dao = db.reminderDao()
    }

    @After
    fun tearDown() = db.close()

    private fun reminder(id: String, status: ReminderStatus, trigger: Long, rebuzz: Long? = null) =
        ReminderEntity(
            id = id, text = id, dueLocal = "2026-09-23T16:30:00", triggerAtMillis = trigger,
            status = status.wire, origin = "requested", rebuzzAtMillis = rebuzz,
            createdAtMillis = 0, updatedAtMillis = 0,
        )

    @Test
    fun nextTriggerIsNullWhenNothingIsScheduled() = runBlocking {
        dao.upsert(reminder("done", ReminderStatus.DONE, 100))
        dao.upsert(reminder("gone", ReminderStatus.CANCELLED, 50))
        assertNull(dao.nextTrigger())
    }

    @Test
    fun nextTriggerIsTheEarliestScheduledOrRebuzz() = runBlocking {
        dao.upsert(reminder("a", ReminderStatus.PENDING, 500))
        dao.upsert(reminder("b", ReminderStatus.SNOOZED, 400))
        dao.upsert(reminder("c", ReminderStatus.DEFERRED, 300))
        dao.upsert(reminder("done", ReminderStatus.DONE, 100))
        assertEquals(300L, dao.nextTrigger())
        dao.upsert(reminder("fired", ReminderStatus.FIRED, 50, rebuzz = 200))
        assertEquals(200L, dao.nextTrigger())
    }

    @Test
    fun dueQueries() = runBlocking {
        dao.upsert(reminder("early", ReminderStatus.PENDING, 100))
        dao.upsert(reminder("late", ReminderStatus.PENDING, 1_000))
        dao.upsert(reminder("fired", ReminderStatus.FIRED, 50, rebuzz = 150))
        assertEquals(listOf("early"), dao.scheduledDueBy(200).map { it.id })
        assertEquals(listOf("fired"), dao.rebuzzDueBy(200).map { it.id })
        assertEquals(setOf("early", "late", "fired"), dao.active().map { it.id }.toSet())
    }

    @Test
    fun purgeOnlyRemovesOldFinishedRows() = runBlocking {
        dao.upsert(reminder("oldDone", ReminderStatus.DONE, 0).copy(updatedAtMillis = 10))
        dao.upsert(reminder("newDone", ReminderStatus.DONE, 0).copy(updatedAtMillis = 1_000))
        dao.upsert(reminder("oldPending", ReminderStatus.PENDING, 0).copy(updatedAtMillis = 10))
        dao.purgeFinishedBefore(100)
        assertEquals(setOf("newDone", "oldPending"), dao.all().map { it.id }.toSet())
    }
}
