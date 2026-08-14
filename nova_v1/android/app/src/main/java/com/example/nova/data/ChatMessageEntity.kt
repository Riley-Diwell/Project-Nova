package com.example.nova.data

import androidx.room.Entity
import androidx.room.PrimaryKey
import com.example.nova.model.ChatMessage

/** Room-persisted row backing one [ChatMessage] in the Voice screen's thread. */
@Entity(tableName = "chat_messages")
data class ChatMessageEntity(
    @PrimaryKey val id: String,
    val text: String,
    val fromUser: Boolean,
    val timestamp: Long,
)

fun ChatMessageEntity.toChatMessage(): ChatMessage = ChatMessage(id = id, text = text, fromUser = fromUser)

fun ChatMessage.toEntity(timestamp: Long): ChatMessageEntity =
    ChatMessageEntity(id = id, text = text, fromUser = fromUser, timestamp = timestamp)
