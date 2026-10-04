package com.example.novav2.data

import androidx.room.Entity
import androidx.room.PrimaryKey
import com.example.novav2.model.ChatMessage

/** Room-persisted row backing one [ChatMessage] in the Voice screen's thread. */
@Entity(tableName = "chat_messages")
data class ChatMessageEntity(
    @PrimaryKey val id: String,
    val text: String,
    val fromUser: Boolean,
    val timestamp: Long,
    /**
     * The notes whose words this bubble holds - the turn that saved a note, or read one back - as
     * [NoteIdsColumn] writes them. Deleting a note deletes these rows (deleted notes are entirely
     * gone, docs/plans/notes-hard-delete-plan.md S9). Null for every other bubble.
     */
    val noteIds: String? = null,
)

fun ChatMessageEntity.toChatMessage(): ChatMessage = ChatMessage(id = id, text = text, fromUser = fromUser)

fun ChatMessage.toEntity(timestamp: Long, noteIds: Collection<String> = emptyList()): ChatMessageEntity =
    ChatMessageEntity(
        id = id, text = text, fromUser = fromUser, timestamp = timestamp,
        noteIds = NoteIdsColumn.encode(noteIds),
    )

/**
 * A set of note ids in one TEXT column: ",id1,id2," - delimited at both ends, so
 * [ChatMessageDao.deleteForNote]'s `LIKE '%,' || id || ',%'` matches a whole id and never a prefix
 * of one. Note ids are UUIDs: no commas, no LIKE wildcards.
 */
object NoteIdsColumn {
    fun encode(ids: Collection<String>): String? {
        val clean = ids.map { it.trim() }.filter { it.isNotEmpty() && ',' !in it }.distinct()
        return if (clean.isEmpty()) null else clean.joinToString(",", prefix = ",", postfix = ",")
    }

    fun decode(column: String?): List<String> =
        column?.split(',')?.filter { it.isNotEmpty() }.orEmpty()
}
