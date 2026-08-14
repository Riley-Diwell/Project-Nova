package com.example.nova.model

import java.util.UUID

/** One bubble in the Voice screen's message thread - persisted via [com.example.nova.data.NovaDatabase]. */
data class ChatMessage(
    val id: String = UUID.randomUUID().toString(),
    val text: String,
    val fromUser: Boolean,
)
