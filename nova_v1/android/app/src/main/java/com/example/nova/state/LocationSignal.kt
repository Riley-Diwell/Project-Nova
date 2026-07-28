package com.example.nova.state

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority

/**
 * Cheap signal (CONTEXT.md "User State" - ambient sensor inference): coarse location, cached
 * from the last successful fix. Requires ACCESS_COARSE_LOCATION; [latestLocationCtx] is
 * best-effort and may stay null until the first fix lands after [refresh] is called.
 * Rounded to ~100m to keep the on-device signal coarse (Privacy pillar).
 */
object LocationSignal {
    @Volatile
    var latestLocationCtx: String? = null
        private set

    fun hasPermission(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_COARSE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    @SuppressLint("MissingPermission")
    fun refresh(context: Context) {
        if (!hasPermission(context)) return
        LocationServices.getFusedLocationProviderClient(context)
            .getCurrentLocation(Priority.PRIORITY_BALANCED_POWER_ACCURACY, null)
            .addOnSuccessListener { location ->
                if (location != null) {
                    latestLocationCtx = "%.3f,%.3f".format(location.latitude, location.longitude)
                }
            }
    }
}
