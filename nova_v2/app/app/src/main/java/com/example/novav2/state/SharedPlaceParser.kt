package com.example.novav2.state

import java.net.URI
import java.net.URLDecoder

/**
 * Reads a place out of what Google Maps puts on the share sheet - usually the place's name (and
 * sometimes its address) followed by a link, e.g. "Birch Building\nhttps://maps.app.goo.gl/Ab12".
 * Pure, so it can be tested without Android; [SharedPlaceResolver] does the network and Geocoder
 * parts (following that short link, or geocoding the name when no link carries coordinates).
 */
object SharedPlaceParser {
    private val URL = Regex("""https?://\S+""")
    private val NUMBER = """(-?\d{1,3}(?:\.\d+)?)"""
    /** The place's own pin in a /maps/place/ URL's data blob - exact, unlike the viewport. */
    private val PIN = Regex("""!3d$NUMBER!4d$NUMBER""")
    /** The map's viewport centre (/maps/place/Name/@lat,lng,17z) - near the place, not on it. */
    private val VIEWPORT = Regex("""/@$NUMBER,$NUMBER""")
    private val PAIR = Regex("""^\s*$NUMBER\s*,\s*$NUMBER\s*$""")
    /** Query parameters Maps uses for a point, most specific first. */
    private val POINT_PARAMS = listOf("q", "query", "destination", "daddr", "ll", "center")

    fun firstUrl(text: String): String? = URL.find(text)?.value?.trimEnd('.', ',', ')', ']')

    /** Coordinates written into [url] itself, or null if it only names the place. */
    fun coordinates(url: String): Pair<Double, Double>? {
        PIN.find(url)?.let { return pair(it.groupValues[1], it.groupValues[2]) }
        val params = queryParams(url)
        for (name in POINT_PARAMS) {
            val match = params[name]?.let { PAIR.find(it) } ?: continue
            pair(match.groupValues[1], match.groupValues[2])?.let { return it }
        }
        // geo:-35.27,149.11?q=... - the form Maps hands to other apps.
        if (url.startsWith("geo:")) {
            PAIR.find(url.removePrefix("geo:").substringBefore('?'))?.let {
                pair(it.groupValues[1], it.groupValues[2])?.let { p -> if (p != Pair(0.0, 0.0)) return p }
            }
        }
        VIEWPORT.find(url)?.let { return pair(it.groupValues[1], it.groupValues[2]) }
        return null
    }

    /** The place as [url] names it in words - the q= text or the /maps/place/<name>/ segment -
     * for geocoding when it carries no coordinates. Null for a bare coordinate pair. */
    fun placeName(url: String): String? {
        val params = queryParams(url)
        for (name in POINT_PARAMS) {
            val value = params[name]?.takeIf { it.isNotBlank() } ?: continue
            if (PAIR.matches(value)) continue
            return value
        }
        val path = runCatching { URI(url).rawPath }.getOrNull() ?: return null
        val segment = path.substringAfter("/maps/place/", "").substringBefore('/')
        return segment.takeIf { it.isNotBlank() }?.let(::decode)
    }

    /** What the user would call it: the shared text with the link taken out, first line - else
     * [placeName] from the link. */
    fun label(text: String, url: String?): String? {
        val words = text.replace(URL, "").lines().map { it.trim() }.firstOrNull { it.isNotEmpty() }
        return words ?: url?.let(::placeName)
    }

    /** The shared text without its link, lines joined - name plus address geocodes best. */
    fun searchText(text: String): String? =
        text.replace(URL, "").lines().map { it.trim().trimEnd(',') }.filter { it.isNotEmpty() }
            .joinToString(", ").takeIf { it.isNotBlank() }

    /** consent.google.com wraps the real URL in ?continue= - unwraps it, else returns [url]. */
    fun unwrap(url: String): String =
        if (runCatching { URI(url).host }.getOrNull()?.startsWith("consent.") == true) {
            queryParams(url)["continue"] ?: url
        } else url

    private fun queryParams(url: String): Map<String, String> {
        val query = runCatching { URI(url).rawQuery }.getOrNull() ?: return emptyMap()
        return query.split('&').mapNotNull { part ->
            val key = part.substringBefore('=', "")
            if (key.isEmpty()) null else key to decode(part.substringAfter('='))
        }.toMap()
    }

    private fun decode(value: String): String = runCatching { URLDecoder.decode(value, "UTF-8") }.getOrDefault(value)

    private fun pair(lat: String, lng: String): Pair<Double, Double>? {
        val la = lat.toDoubleOrNull() ?: return null
        val ln = lng.toDoubleOrNull() ?: return null
        return if (la in -90.0..90.0 && ln in -180.0..180.0) Pair(la, ln) else null
    }
}
