package com.example.nova.viewmodel

import android.app.Application
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.example.nova.data.NovaDatabase
import com.example.nova.data.toChatMessage
import com.example.nova.data.toEntity
import com.example.nova.model.ChatMessage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Holds the Voice screen's message thread and mirrors every turn to [NovaDatabase] so it survives
 * leaving the tab or closing the app - [VoiceScreen][com.example.nova.ui.screens.VoiceScreen] used
 * to hold this in plain `remember` state, which neither did.
 */
class ChatViewModel(application: Application) : AndroidViewModel(application) {
    private val dao = NovaDatabase.getInstance(application).chatMessageDao()

    val messages: SnapshotStateList<ChatMessage> = mutableStateListOf()

    init {
        viewModelScope.launch {
            val loaded = withContext(Dispatchers.IO) { dao.getAll() }
            messages.addAll(loaded.map { it.toChatMessage() })
        }
    }

    fun addMessage(text: String, fromUser: Boolean): ChatMessage {
        val message = ChatMessage(text = text, fromUser = fromUser)
        messages.add(message)
        val timestamp = System.currentTimeMillis()
        viewModelScope.launch(Dispatchers.IO) {
            dao.insert(message.toEntity(timestamp))
        }
        return message
    }

    fun clearMessages() {
        messages.clear()
        viewModelScope.launch(Dispatchers.IO) {
            dao.clearAll()
        }
    }
}
