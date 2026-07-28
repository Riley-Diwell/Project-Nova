package com.example.nova.state

import android.Manifest
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat
import com.google.android.gms.location.ActivityRecognition

/**
 * Cheap signal (DESIGN.md §5.2): coarse activity from Android's ActivityRecognition API.
 * Updates arrive in batches (not instantly) via [ActivityTransitionReceiver], so the latest
 * value may be up to [UPDATE_INTERVAL_MILLIS] stale or null until the first batch lands.
 */
object ActivitySignal {
    private const val UPDATE_INTERVAL_MILLIS = 60_000L
    private const val REQUEST_CODE = 1001

    @Volatile
    var latestActivity: String? = null
        internal set

    fun hasPermission(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return true
        return ContextCompat.checkSelfPermission(context, Manifest.permission.ACTIVITY_RECOGNITION) ==
            PackageManager.PERMISSION_GRANTED
    }

    fun startUpdates(context: Context) {
        if (!hasPermission(context)) return
        ActivityRecognition.getClient(context)
            .requestActivityUpdates(UPDATE_INTERVAL_MILLIS, pendingIntent(context))
    }

    fun stopUpdates(context: Context) {
        ActivityRecognition.getClient(context).removeActivityUpdates(pendingIntent(context))
    }

    private fun pendingIntent(context: Context): PendingIntent {
        val intent = Intent(context, ActivityTransitionReceiver::class.java)
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE
        return PendingIntent.getBroadcast(context, REQUEST_CODE, intent, flags)
    }
}
