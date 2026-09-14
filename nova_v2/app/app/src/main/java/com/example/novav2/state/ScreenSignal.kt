package com.example.novav2.state

import android.content.Context
import android.os.PowerManager

/** Cheap signal (DESIGN.md §5.2): whether the screen is currently on. No permission needed. */
object ScreenSignal {
    fun isScreenOn(context: Context): Boolean {
        val powerManager = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        return powerManager.isInteractive
    }
}
