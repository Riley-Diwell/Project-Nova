package com.example.novav2.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import com.example.novav2.ble.NovaDevicePairing

/**
 * Restarts NovaDeviceService after a reboot, without waiting for the user to open the
 * app first - a paired wearable is supposed to work without reaching for the phone
 * (see NovaDeviceService's own "stays connected like AirPods" framing). Without this,
 * a device paired before a reboot would just sit unconnected - nothing else re-starts
 * that service except MainActivity's own onCreate (see there), which still needs the
 * app to actually be opened once.
 */
class BootCompletedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        if (!NovaDevicePairing.isPaired(context)) return
        ContextCompat.startForegroundService(context, Intent(context, NovaDeviceService::class.java))
    }
}
