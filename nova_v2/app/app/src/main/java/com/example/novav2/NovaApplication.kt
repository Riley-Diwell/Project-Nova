package com.example.novav2

import android.app.Application
import com.example.novav2.auth.AuthRepository
import com.example.novav2.knowledge.KnowledgeRepository
import com.example.novav2.profile.ProfileRepository

/**
 * Runs before any activity, service or receiver, so everything that calls the server - including
 * services BootCompletedReceiver starts with no UI - finds the signed-in session already loaded.
 */
class NovaApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        AuthRepository.init(this)
        ProfileRepository.init(this)
        KnowledgeRepository.init(this)
    }
}
