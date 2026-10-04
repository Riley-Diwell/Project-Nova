package com.example.novav2.notes.data

import android.content.Context
import androidx.room.AutoMigration
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.Transaction
import kotlinx.coroutines.flow.Flow

/**
 * A note waiting to reach the server - the outbox that means "no note is ever lost offline".
 * [payload] is the exact POST /notes body, so a retry sends
 * byte-for-byte what the first attempt did; its `id` makes the retry idempotent server-side.
 */
@Entity(tableName = "pending_notes")
data class PendingNoteEntity(
    @PrimaryKey val id: String,
    val payload: String,
    val createdAtMillis: Long,
    /** Ask for a summary once it lands (a long dictation or capture). */
    val wantsSummary: Boolean,
    /** For the "saved" notification once it finally lands. */
    val preview: String,
    val attempts: Int = 0,
    val lastError: String? = null,
)

@Dao
interface PendingNoteDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(note: PendingNoteEntity)

    @Query("SELECT * FROM pending_notes ORDER BY createdAtMillis ASC")
    suspend fun all(): List<PendingNoteEntity>

    @Query("SELECT COUNT(*) FROM pending_notes")
    suspend fun count(): Int

    @Query("DELETE FROM pending_notes WHERE id = :id")
    suspend fun delete(id: String)

    @Query("SELECT COUNT(*) FROM pending_notes WHERE id = :id")
    suspend fun has(id: String): Int

    @Query("DELETE FROM pending_notes")
    suspend fun clear()

    @Query("UPDATE pending_notes SET attempts = attempts + 1, lastError = :error WHERE id = :id")
    suspend fun recordFailure(id: String, error: String)
}

/**
 * A note the user deleted that the server may still have - the delete waiting to reach it.
 * Ids only, never content: the note's own rows on this phone are gone the moment the user
 * deletes it (docs/plans/notes-hard-delete-plan.md S13), and only this remains until the server
 * confirms. [ALL] stands for "Delete all notes".
 */
@Entity(tableName = "pending_note_deletes")
data class PendingNoteDeleteEntity(
    @PrimaryKey val id: String,
    val requestedAtMillis: Long,
) {
    companion object {
        const val ALL = "*"
    }
}

@Dao
interface PendingNoteDeleteDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(delete: PendingNoteDeleteEntity)

    @Query("SELECT * FROM pending_note_deletes ORDER BY requestedAtMillis ASC")
    suspend fun all(): List<PendingNoteDeleteEntity>

    @Query("SELECT COUNT(*) FROM pending_note_deletes WHERE id = :id OR id = '*'")
    suspend fun isPending(id: String): Int

    @Query("DELETE FROM pending_note_deletes WHERE id = :id")
    suspend fun delete(id: String)
}

/**
 * One row of the Notes list, as the server last sent it (GET /notes' list item, minus the
 * search-only snippet) - the list's cache, so the tab opens on what it showed last time, even
 * offline, and is revalidated behind it. Only ever a copy: the server is the truth, and a
 * refresh replaces the lot ([NoteRowDao.replaceAll]).
 */
@Entity(tableName = "note_rows")
data class NoteRowEntity(
    @PrimaryKey val id: String,
    val createdAtMillis: Long,
    val source: String,
    val kind: String,
    val title: String,
    val preview: String,
    val tldr: String?,
    val durationS: Double?,
    val calendarTitle: String?,
    val summaryStatus: String,
    val promoted: Boolean,
)

@Dao
interface NoteRowDao {
    @Query("SELECT * FROM note_rows ORDER BY createdAtMillis DESC")
    fun observe(): Flow<List<NoteRowEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(rows: List<NoteRowEntity>)

    @Query("DELETE FROM note_rows WHERE id = :id")
    suspend fun delete(id: String)

    @Query("DELETE FROM note_rows")
    suspend fun clear()

    /** The list exactly as the server now has it: rows it no longer sends are dropped. */
    @Transaction
    suspend fun replaceAll(rows: List<NoteRowEntity>) {
        clear()
        upsert(rows)
    }
}

/**
 * The notes feature's own small database, separate from [com.example.novav2.data.NovaDatabase]
 * on purpose: that one's version and migrations are being moved by the reminders work at the
 * same time, and an outbox table has no relationship to chat history or reminders that would
 * justify sharing a schema version (and a migration chain) with them. Two files, no coupling.
 *
 * Schemas export to app/schemas/ like NovaDatabase's (build.gradle.kts' room.schemaLocation).
 * Never add fallbackToDestructiveMigration(): it would drop notes that haven't reached the
 * server yet.
 */
@Database(
    entities = [PendingNoteEntity::class, NoteRowEntity::class, PendingNoteDeleteEntity::class],
    version = 3,
    exportSchema = true,
    // 2: the list cache. A new table, so the outbox is untouched.
    // 3: deletes waiting to reach the server. A new table again.
    autoMigrations = [AutoMigration(from = 1, to = 2), AutoMigration(from = 2, to = 3)],
)
abstract class NotesDatabase : RoomDatabase() {
    abstract fun pendingNoteDao(): PendingNoteDao
    abstract fun noteRowDao(): NoteRowDao
    abstract fun pendingNoteDeleteDao(): PendingNoteDeleteDao

    companion object {
        @Volatile private var INSTANCE: NotesDatabase? = null

        fun getInstance(context: Context): NotesDatabase =
            INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(
                    context.applicationContext, NotesDatabase::class.java, "nova_notes.db",
                ).build().also { INSTANCE = it }
            }
    }
}
