package com.example.novav2.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface ChatMessageDao {
    @Insert
    suspend fun insert(message: ChatMessageEntity)

    /** Room re-runs this and re-emits on every write to chat_messages, from any writer in the
     * process - not just [insert] calls made through this same Dao instance. ChatViewModel needs
     * that: a turn AssistVoiceService inserts while the app isn't open is written through a
     * separate Dao instance, so a one-shot load would never pick it up once the ViewModel (and
     * the process behind it - SignalMonitorService keeps it alive) has already been created. */
    @Query("SELECT * FROM chat_messages ORDER BY timestamp ASC")
    fun observeAll(): Flow<List<ChatMessageEntity>>

    @Query("DELETE FROM chat_messages")
    suspend fun clearAll()
}
