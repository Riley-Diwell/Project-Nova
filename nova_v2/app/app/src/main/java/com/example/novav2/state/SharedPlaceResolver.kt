package com.example.novav2.state

import android.content.Context
import android.location.Geocoder
import android.os.Build
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.Locale
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume

/**
 * Turns a place shared from Google Maps into coordinates for [DeviceCompass]. Maps' share link is
 * usually a maps.app.goo.gl short link, so this follows its redirects one hop at a time - the
 * coordinates can sit in any hop - and falls back to geocoding the place's name on-device (the
 * same Geocoder [PlaceNameLookup] uses) when none of them carries any.
 */
object SharedPlaceResolver {
    data class Place(val latitude: Double, val longitude: Double, val label: String?)

    private const val MAX_REDIRECTS = 6

    private val http = OkHttpClient.Builder()
        .followRedirects(false)
        .followSslRedirects(false)
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.SECONDS)
        .build()

    /** Null when [text] doesn't lead anywhere - not a place, offline, or nothing geocodes. */
    suspend fun resolve(context: Context, text: String): Place? {
        val url = SharedPlaceParser.firstUrl(text)
        val hops = url?.let { followRedirects(it) } ?: emptyList()
        val finalUrl = hops.lastOrNull()
        val label = SharedPlaceParser.label(text, finalUrl)

        hops.firstNotNullOfOrNull(SharedPlaceParser::coordinates)?.let { (lat, lng) ->
            return Place(lat, lng, label)
        }
        val query = SharedPlaceParser.searchText(text) ?: finalUrl?.let(SharedPlaceParser::placeName) ?: return null
        return geocode(context, query)?.let { (lat, lng) -> Place(lat, lng, label ?: query) }
    }

    /** [url] and every URL it redirects through, in order. Stops at the first failure with what
     * it has - a short link that can't be followed still leaves the shared text to geocode. */
    private suspend fun followRedirects(url: String): List<String> = withContext(Dispatchers.IO) {
        val hops = mutableListOf(SharedPlaceParser.unwrap(url))
        repeat(MAX_REDIRECTS) {
            val current = hops.last()
            // Already has a point - no need to ask the network anything.
            if (SharedPlaceParser.coordinates(current) != null) return@withContext hops
            val next = runCatching {
                http.newCall(Request.Builder().url(current).get().build()).execute().use { response ->
                    if (response.isRedirect) response.header("Location")?.let { response.request.url.resolve(it)?.toString() }
                    else null
                }
            }.getOrNull() ?: return@withContext hops
            hops += SharedPlaceParser.unwrap(next)
        }
        hops
    }

    private suspend fun geocode(context: Context, query: String): Pair<Double, Double>? {
        if (!Geocoder.isPresent()) return null
        val geocoder = Geocoder(context, Locale.getDefault())
        return try {
            val address = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                suspendCancellableCoroutine { continuation ->
                    geocoder.getFromLocationName(query, 1, object : Geocoder.GeocodeListener {
                        override fun onGeocode(addresses: MutableList<android.location.Address>) {
                            continuation.resume(addresses.firstOrNull())
                        }
                        override fun onError(errorMessage: String?) {
                            continuation.resume(null)
                        }
                    })
                }
            } else {
                withContext(Dispatchers.IO) {
                    @Suppress("DEPRECATION")
                    geocoder.getFromLocationName(query, 1)?.firstOrNull()
                }
            }
            address?.let { Pair(it.latitude, it.longitude) }
        } catch (e: Exception) {
            // Network-backed on most devices - offline or rate-limited is normal (see PlaceNameLookup).
            null
        }
    }
}
