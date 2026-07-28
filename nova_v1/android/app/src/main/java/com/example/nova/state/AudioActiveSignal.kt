package com.example.nova.state

import android.content.Context
import android.media.AudioManager

/** No-permission signal: whether the system is currently playing music or audio. */
object AudioActiveSignal {
    fun isMusicActive(context: Context): Boolean {
        val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        return audioManager.isMusicActive
    }
}
