package com.example.nova.state

import android.content.Context
import android.media.AudioManager

/** No-permission signal: current ringer mode (silent / vibrate / normal). */
object RingerModeSignal {
    fun currentRingerMode(context: Context): String {
        val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        return when (audioManager.ringerMode) {
            AudioManager.RINGER_MODE_SILENT -> "silent"
            AudioManager.RINGER_MODE_VIBRATE -> "vibrate"
            AudioManager.RINGER_MODE_NORMAL -> "normal"
            else -> "unknown"
        }
    }
}
