package com.example.novav2.model

import java.util.UUID

/** One bubble in the Voice screen's message thread - persisted via [com.example.novav2.data.NovaDatabase]. */
data class ChatMessage(
    val id: String = UUID.randomUUID().toString(),
    val text: String,
    val fromUser: Boolean,
)
