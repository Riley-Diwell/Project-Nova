package com.example.nova.state

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager

/** Cheap signal: whether wired or Bluetooth audio output is currently routed. No permission needed. */
object AudioRouteSignal {
    data class AudioRoute(val wiredHeadsetConnected: Boolean, val bluetoothAudioConnected: Boolean)

    fun currentAudioRoute(context: Context): AudioRoute {
        val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val devices = audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
        var wired = false
        var bluetooth = false
        for (device in devices) {
            when (device.type) {
                AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_WIRED_HEADPHONES -> wired = true
                AudioDeviceInfo.TYPE_BLUETOOTH_A2DP, AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> bluetooth = true
            }
        }
        return AudioRoute(wired, bluetooth)
    }
}
