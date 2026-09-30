package com.example.novav2.data

import android.content.Context
import androidx.room.AutoMigration
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

/**
 * Schemas are exported to app/schemas/ (see build.gradle.kts) and committed - every version
 * bump needs the previous version's JSON there for its AutoMigration to be generated.
 *
 * Never add fallbackToDestructiveMigration(): it would wipe the chat history on any schema
 * mistake. A new table is an AutoMigration; anything else needs a written Migration.
 *
 *   1 -> 2  reminders table
 *   2 -> 3  reminders.dirty, for syncing to the account (state/ReminderSync.kt)
 *   3 -> 4  reminders.clearedAtMillis, for swiping done reminders off the list
 */
@Database(
    entities = [ChatMessageEntity::class, ReminderEntity::class],
    version = 4,
    exportSchema = true,
    autoMigrations = [AutoMigration(from = 1, to = 2), AutoMigration(from = 2, to = 3), AutoMigration(from = 3, to = 4)],
)
abstract class NovaDatabase : RoomDatabase() {
    abstract fun chatMessageDao(): ChatMessageDao
    abstract fun reminderDao(): ReminderDao

    companion object {
        @Volatile
        private var INSTANCE: NovaDatabase? = null

        fun getInstance(context: Context): NovaDatabase =
            INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(
                    context.applicationContext,
                    NovaDatabase::class.java,
                    "nova.db",
                ).build().also { INSTANCE = it }
            }
    }
}
