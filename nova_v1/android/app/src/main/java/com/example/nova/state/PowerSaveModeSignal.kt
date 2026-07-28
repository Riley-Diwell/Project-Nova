package com.example.nova.state

import android.content.Context
import android.os.PowerManager

/** No-permission signal: whether the device is currently in battery saver / power save mode. */
object PowerSaveModeSignal {
    fun isPowerSaveMode(context: Context): Boolean {
        val powerManager = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        return powerManager.isPowerSaveMode
    }
}
