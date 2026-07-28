package com.example.nova.state

import android.content.Context
import android.provider.Settings

/** No-permission signal: whether airplane mode is currently enabled. */
object AirplaneModeSignal {
    fun isAirplaneModeOn(context: Context): Boolean =
        Settings.Global.getInt(context.contentResolver, Settings.Global.AIRPLANE_MODE_ON, 0) != 0
}
