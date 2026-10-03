package com.example.novav2.data

import androidx.room.Room
import androidx.room.testing.MigrationTestHelper
import androidx.sqlite.db.framework.FrameworkSQLiteOpenHelperFactory
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Every step keeps what was there (reads app/schemas/): 1 -> 2 adds the reminders table, 2 -> 3
 * the sync `dirty` flag, 3 -> 4 `clearedAtMillis` for swiping done reminders away.
 */
@RunWith(AndroidJUnit4::class)
class MigrationTest {
    private val dbName = "migration-test"

    @get:Rule
    val helper = MigrationTestHelper(
        InstrumentationRegistry.getInstrumentation(),
        NovaDatabase::class.java,
        emptyList(),
        FrameworkSQLiteOpenHelperFactory(),
    )

    @Test
    fun migrate1To2KeepsChatMessages() {
        helper.createDatabase(dbName, 1).apply {
            execSQL(
                "INSERT INTO chat_messages (id, text, fromUser, timestamp) " +
                    "VALUES ('m1', 'remind me to call mum', 1, 1000)"
            )
            close()
        }

        helper.runMigrationsAndValidate(dbName, 2, true)

        val db = Room.databaseBuilder(
            ApplicationProvider.getApplicationContext(), NovaDatabase::class.java, dbName,
        ).build()
        try {
            val messages = runBlocking { db.chatMessageDao().observeAll().first() }
            assertEquals(listOf("remind me to call mum"), messages.map { it.text })
            assertEquals(0, runBlocking { db.reminderDao().all() }.size)
        } finally {
            db.close()
        }
    }

    @Test
    fun migrate2To3KeepsRemindersAndLeavesThemClean() {
        helper.createDatabase(dbName, 2).apply {
            execSQL(INSERT_V2_REMINDER)
            close()
        }

        helper.runMigrationsAndValidate(dbName, 3, true).apply {
            query("SELECT text, dirty FROM reminders WHERE id = 'r1'").use {
                it.moveToFirst()
                assertEquals("Email Dr Chen", it.getString(0))
                assertEquals(0, it.getInt(1))
            }
            close()
        }
    }

    @Test
    fun migrate3To4KeepsRemindersUncleared() {
        helper.createDatabase(dbName, 2).apply {
            execSQL(INSERT_V2_REMINDER)
            close()
        }
        helper.runMigrationsAndValidate(dbName, 3, true).close()

        helper.runMigrationsAndValidate(dbName, 4, true).close()

        val db = Room.databaseBuilder(
            ApplicationProvider.getApplicationContext(), NovaDatabase::class.java, dbName,
        ).build()
        try {
            val r = runBlocking { db.reminderDao().byId("r1") }!!
            assertEquals("Email Dr Chen", r.text)
            assertEquals("2026-09-23T16:30:00", r.dueLocal)
            assertEquals("weekly", r.recurFrequency)
            assertNull(r.clearedAtMillis)
        } finally {
            db.close()
        }
    }

    @Test
    fun migrate4To5KeepsRemindersTimed() {
        helper.createDatabase(dbName, 2).apply {
            execSQL(INSERT_V2_REMINDER)
            close()
        }
        helper.runMigrationsAndValidate(dbName, 3, true).close()
        helper.runMigrationsAndValidate(dbName, 4, true).close()

        helper.runMigrationsAndValidate(dbName, 5, true).close()

        val db = Room.databaseBuilder(
            ApplicationProvider.getApplicationContext(), NovaDatabase::class.java, dbName,
        ).build()
        try {
            val r = runBlocking { db.reminderDao().byId("r1") }!!
            assertEquals("2026-09-23T16:30:00", r.dueLocal)
            assertNull(r.placeOn)
            assertNull(r.place)
            assertEquals(false, r.everyTime)
        } finally {
            db.close()
        }
    }

    private companion object {
        /** A row in the version 2 shape - no dirty, no clearedAtMillis. */
        const val INSERT_V2_REMINDER =
            "INSERT INTO reminders (id, text, dueLocal, triggerAtMillis, status, priority, origin, " +
                "outcomeReported, deferCount, snoozeCount, recurFrequency, recurInterval, " +
                "createdAtMillis, updatedAtMillis) VALUES ('r1', 'Email Dr Chen', " +
                "'2026-09-23T16:30:00', 1790000000000, 'pending', 'normal', 'requested', " +
                "0, 0, 0, 'weekly', 1, 1789990000000, 1789990000000)"
    }
}
