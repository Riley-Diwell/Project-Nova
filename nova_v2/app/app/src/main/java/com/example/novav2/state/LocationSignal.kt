package com.example.novav2.state

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority

/**
 * Cheap signal (CONTEXT.md "User State" - ambient sensor inference): fine-accuracy location,
 * cached from the last successful fix. Requires ACCESS_FINE_LOCATION (ACCESS_COARSE_LOCATION is
 * still declared as a fallback for devices/users that only grant that); [latestLocationCtx] is
 * best-effort and may stay null until the first fix lands after [refresh] is called.
 *
 * Rounded to ~11m (4 decimal places) rather than left at full precision - that's well under
 * typical GPS jitter, so a stationary phone still re-snapshots the same string on every 10s
 * tick (see [PlaceNameLookup]'s cache and the backend's own reverse-geocode), while still being
 * far tighter than the ~100m rounding this used to carry when the signal was coarse-only. That
 * old coarse-only design (ACCESS_COARSE_LOCATION + PRIORITY_BALANCED_POWER_ACCURACY, in the name
 * of the Privacy pillar) meant Android's OS-level "approximate location" fuzzing could put a
 * fix hundreds of meters to a few km off - which showed up as visibly wrong "where am I" answers,
 * even though every other consumer of location_ctx (ambient confidence scoring, habitual-place
 * matching, travel-time estimates) only ever needed a rough signal. Fine accuracy trades a
 * bigger permission ask for actually-correct addresses on that one path.
 */
object LocationSignal {
    @Volatile
    var latestLocationCtx: String? = null
        private set

    fun hasPermission(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_COARSE_LOCATION) ==
                PackageManager.PERMISSION_GRANTED

    @SuppressLint("MissingPermission")
    fun refresh(context: Context) {
        if (!hasPermission(context)) return
        LocationServices.getFusedLocationProviderClient(context)
            .getCurrentLocation(Priority.PRIORITY_HIGH_ACCURACY, null)
            .addOnSuccessListener { location ->
                if (location != null) {
                    latestLocationCtx = "%.4f,%.4f".format(location.latitude, location.longitude)
                }
            }
    }
}
