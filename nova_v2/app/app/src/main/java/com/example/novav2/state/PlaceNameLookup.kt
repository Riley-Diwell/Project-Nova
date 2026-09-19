package com.example.novav2.state

import android.content.Context
import android.location.Address
import android.location.Geocoder
import android.os.Build
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.util.Locale
import kotlin.coroutines.resume

/**
 * Turns [LocationSignal]'s "lat,lng" into a place name, entirely on-device via Android's
 * Geocoder - no network round trip to the backend, and no new API key. Debug display only:
 * the wire's location_ctx stays a bare coordinate pair (see UserState.kt) - the backend's own
 * get_current_address tool already does its own reverse-geocode when a turn actually needs one,
 * and duplicating that here would just be a second, possibly-disagreeing answer to the same
 * question.
 *
 * Cached by the exact "lat,lng" string, since LocationSignal already rounds to ~100m - a phone
 * that hasn't moved re-snapshots the same string every 10s and shouldn't re-geocode it.
 */
object PlaceNameLookup {
    private val cache = mutableMapOf<String, String?>()

    /** Null while unresolved (unknown location, no geocoder on this device, or the lookup
     * genuinely found nothing) - callers show a plain "Unknown" for all three, same as any
     * other signal with nothing to report. */
    suspend fun lookup(context: Context, locationCtx: String?): String? {
        if (locationCtx == null) return null
        if (cache.containsKey(locationCtx)) return cache[locationCtx]

        val parts = locationCtx.split(",")
        val lat = parts.getOrNull(0)?.toDoubleOrNull()
        val lng = parts.getOrNull(1)?.toDoubleOrNull()
        if (lat == null || lng == null || !Geocoder.isPresent()) {
            cache[locationCtx] = null
            return null
        }

        val label = try {
            geocode(Geocoder(context, Locale.getDefault()), lat, lng)?.let(::formatAddress)
        } catch (e: Exception) {
            // Geocoder is backed by a network service on most devices - offline, rate-limited,
            // or simply flaky is normal, not exceptional. Same "say nothing" stance as every
            // other lookup in this codebase that can't reach its source.
            null
        }
        cache[locationCtx] = label
        return label
    }

    private suspend fun geocode(geocoder: Geocoder, lat: Double, lng: Double): Address? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            suspendCancellableCoroutine { continuation ->
                geocoder.getFromLocation(lat, lng, 1) { addresses ->
                    continuation.resume(addresses.firstOrNull())
                }
            }
        } else {
            withContext(Dispatchers.IO) {
                @Suppress("DEPRECATION")
                geocoder.getFromLocation(lat, lng, 1)?.firstOrNull()
            }
        }

    /** Prefers a named place ("Coombs Building") over a bare street address, since that's more
     * useful for a debug reader checking "is Nova seeing roughly where I actually am" - falls
     * back to the street name, then locality, then the geocoder's own formatted line.
     *
     * Android's Geocoder documents featureName as falling back to the house number when there's
     * no real landmark at the address - that number is still != thoroughfare, so without this
     * check it reads as a "named place" and wins outright, throwing away the one thing actually
     * worth showing (the street). [looksLikeHouseNumber] catches that case either by matching
     * subThoroughfare (the field that IS the house number) or, if a geocoder leaves that field
     * empty, by featureName simply being numeric. */
    private fun formatAddress(address: Address): String {
        val locality = address.locality ?: address.subAdminArea
        val featureName = address.featureName
        val looksLikeHouseNumber = featureName != null &&
            (featureName == address.subThoroughfare || featureName.toDoubleOrNull() != null)
        return when {
            featureName != null && featureName != address.thoroughfare && !looksLikeHouseNumber ->
                listOfNotNull(featureName, locality).joinToString(", ")
            address.thoroughfare != null ->
                listOfNotNull(address.thoroughfare, locality).joinToString(", ")
            locality != null -> locality
            else -> address.getAddressLine(0) ?: "Unknown place"
        }
    }
}
