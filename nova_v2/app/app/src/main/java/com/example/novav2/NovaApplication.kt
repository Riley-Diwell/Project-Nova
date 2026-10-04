package com.example.novav2

import android.app.Application
import com.example.novav2.auth.AuthRepository
import com.example.novav2.knowledge.KnowledgeRepository
import com.example.novav2.profile.ProfileRepository
import com.example.novav2.state.DeviceLayers
import com.example.novav2.state.DevicePreferences

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
        // Before any receiver or service sends a layer - DeviceLayers can't read prefs itself.
        DeviceLayers.setColours(DevicePreferences.cueMixes(this))
    }
}
