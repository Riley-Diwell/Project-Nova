package com.example.novav2.state

import android.content.Context
import android.util.Log
import com.example.novav2.ble.NovaDeviceRepository
import com.example.novav2.network.NovaApiClient
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlin.math.abs
import kotlin.math.asin
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Points the device's compass at where the user is going. The firmware only knows which way it
 * is facing; this works out which way it *should* face - the bearing from the phone's latest fix
 * to the destination - and sends it (SET_HEADING, docs/ble-protocol.md). With no destination the
 * device gets no heading and its compass stays dark.
 *
 * The destination is the end of the route behind the latest leave-by
 * ([NovaApiClient.ScheduledDeparture]), set from [DepartureAlarmScheduler.schedule]. It lasts until
 * the user is within [ARRIVED_METERS] of it, or [GRACE_AFTER_START_MILLIS] after the event starts.
 * Acknowledging "leave now" on the device does not end it: that's when the walk begins.
 *
 * A place shared from Google Maps ([com.example.novav2.ShareDestinationActivity]) overrides that:
 * it is the user saying where they're going right now, so a leave-by arriving afterwards doesn't
 * replace it while it lasts - [MANUAL_LIFETIME_MILLIS], or until they get there.
 *
 * Like [DeviceLayers], the phone is the source of truth: the firmware forgets the heading on
 * disconnect and [onConnected] sends it again. [sync] runs on every signal tick (~10 s), so the
 * bearing follows the user as they move.
 */
object DeviceCompass {
    /** Same as navigation.py's ARRIVED_METERS - closer than this, the user is there. */
    const val ARRIVED_METERS = 150.0

    /** Running late still needs the way there - the compass outlives the event's start by this. */
    const val GRACE_AFTER_START_MILLIS = 30 * 60_000L

    /** A departure with no start time (none today - the server only sends one with a calendar
     * anchor) gets this long after its leave-by instead. */
    const val FALLBACK_AFTER_LEAVE_BY_MILLIS = 60 * 60_000L

    /** Bearings closer than this to the last one sent aren't re-sent - GPS jitter would otherwise
     * write to the device every tick for nothing. */
    const val RESEND_THRESHOLD_DEGREES = 2.0

    /** A shared place has no event to end with - it gives up after this if never reached. */
    const val MANUAL_LIFETIME_MILLIS = 2 * 60 * 60_000L

    /** [manual] = shared by the user (see the class comment), [label] = its name, for the toast. */
    data class Target(
        val latitude: Double,
        val longitude: Double,
        val untilMillis: Long,
        val manual: Boolean = false,
        val label: String? = null,
    ) {
        /** Pure: whether a new leave-by should take this one's place - always, unless this is a
         * shared place that is still live. */
        fun yieldsToLeaveBy(nowMillis: Long): Boolean = !manual || nowMillis >= untilMillis

        companion object {
            /** Null when the server had no coordinates (Directions gave none). */
            fun from(departure: NovaApiClient.ScheduledDeparture, nowMillis: Long): Target? {
                val lat = departure.destinationLatitude ?: return null
                val lng = departure.destinationLongitude ?: return null
                val until = departure.minutesUntilStart
                    ?.let { nowMillis + (it * 60_000).toLong() + GRACE_AFTER_START_MILLIS }
                    ?: (nowMillis + (departure.leaveInMinutes.coerceAtLeast(0.0) * 60_000).toLong() +
                        FALLBACK_AFTER_LEAVE_BY_MILLIS)
                return Target(lat, lng, until, label = departure.eventTitle ?: departure.destination)
            }
        }
    }

    /** What the compass should do right now. */
    sealed class Reading {
        /** Point at [degrees], clockwise from true north. */
        data class Heading(val degrees: Double) : Reading()
        /** A destination, but no fix yet to measure from - show nothing for now. */
        data object NoFix : Reading()
        /** The target is done with - reached or expired. */
        data object Finished : Reading()
    }

    /** Pure: [target] as seen from [fix] (latitude, longitude) at [nowMillis]. */
    fun read(target: Target, fix: Pair<Double, Double>?, nowMillis: Long): Reading {
        if (nowMillis >= target.untilMillis) return Reading.Finished
        if (fix == null) return Reading.NoFix
        val (lat, lng) = fix
        if (distanceMeters(lat, lng, target.latitude, target.longitude) <= ARRIVED_METERS) return Reading.Finished
        return Reading.Heading(bearingDegrees(lat, lng, target.latitude, target.longitude))
    }

    private const val TAG = "NovaCompass"
    private const val PREFS = "nova_compass"
    private const val KEY_LAT = "lat"
    private const val KEY_LNG = "lng"
    private const val KEY_UNTIL = "until"
    private const val KEY_MANUAL = "manual"
    private const val KEY_LABEL = "label"

    private val lock = Any()
    private var loaded = false
    private var target: Target? = null
    /** What the device was last told: null = nothing sent on this connection yet. NaN = "none". */
    private var lastSent: Double? = null

    /** What the compass is doing, for the Device screen - [degrees] is null while there's no fix
     * to measure from. */
    data class Status(val label: String?, val manual: Boolean, val degrees: Double?)

    private val _status = MutableStateFlow<Status?>(null)
    /** Null = no destination, compass dark. Kept current by [sync]. */
    val status: StateFlow<Status?> = _status

    /** A new leave-by replaces the destination - or, with no coordinates, removes it, so the
     * compass never points at the previous trip. A live shared place stays put (see the class
     * comment). */
    fun setDestination(context: Context, departure: NovaApiClient.ScheduledDeparture, nowMillis: Long = System.currentTimeMillis()) {
        val next = Target.from(departure, nowMillis)
        val replaced = synchronized(lock) {
            ensureLoaded(context)
            val current = target
            if (current != null && !current.yieldsToLeaveBy(nowMillis)) return@synchronized false
            target = next
            true
        }
        if (replaced) save(context, next)
        sync(context, nowMillis)
    }

    /** The user shared a place from Google Maps - point at it from now on, over any leave-by. */
    fun setSharedPlace(
        context: Context,
        latitude: Double,
        longitude: Double,
        label: String?,
        nowMillis: Long = System.currentTimeMillis(),
    ) {
        val next = Target(latitude, longitude, nowMillis + MANUAL_LIFETIME_MILLIS, manual = true, label = label)
        synchronized(lock) {
            loaded = true
            target = next
        }
        save(context, next)
        sync(context, nowMillis)
        // The fix may be old or missing (no signal tick yet) - aim again once a fresh one lands.
        LocationSignal.refresh(context) { sync(context) }
    }

    /** Whether the user is already within [ARRIVED_METERS] of ([latitude], [longitude]) - so
     * sharing where they're standing can say so instead of silently doing nothing. */
    fun isAlreadyThere(latitude: Double, longitude: Double): Boolean =
        LocationSignal.latestFix?.let { (lat, lng) -> distanceMeters(lat, lng, latitude, longitude) <= ARRIVED_METERS } == true

    private fun ensureLoaded(context: Context) {
        if (!loaded) {
            target = load(context)
            loaded = true
        }
    }

    /** Signing out, or the Device screen's "Clear" - the compass goes dark until the next shared
     * place or leave-by. */
    fun clear(context: Context) {
        synchronized(lock) {
            loaded = true
            target = null
        }
        save(context, null)
        sync(context)
    }

    /** The firmware starts every connection with no heading. */
    fun onConnected(context: Context) {
        synchronized(lock) { lastSent = null }
        sync(context)
        // No fix yet (app just started, say) would leave it dark until the next signal tick.
        if (LocationSignal.latestFix == null) LocationSignal.refresh(context) { sync(context) }
    }

    /** Re-measures from [LocationSignal.latestFix] and tells the device if it changed. */
    fun sync(context: Context, nowMillis: Long = System.currentTimeMillis()) {
        val (reading, current) = synchronized(lock) {
            ensureLoaded(context)
            val reading = target?.let { read(it, LocationSignal.latestFix, nowMillis) }
                ?.also { if (it == Reading.Finished) target = null }
            Pair(reading, target)
        }
        if (reading == Reading.Finished) save(context, null)
        _status.value = current?.let { Status(it.label, it.manual, (reading as? Reading.Heading)?.degrees) }
        send((reading as? Reading.Heading)?.degrees)
    }

    private fun send(degrees: Double?) {
        val sender = NovaDeviceRepository.commandSender.value ?: run {
            synchronized(lock) { lastSent = null } // nothing reached a device - the next connection starts fresh
            return
        }
        synchronized(lock) {
            val wire = degrees ?: Double.NaN
            val previous = lastSent
            if (previous != null && !changedEnough(previous, wire)) return
            lastSent = wire
        }
        Log.d(TAG, if (degrees == null) "SET_HEADING none" else "SET_HEADING %.1f".format(degrees))
        sender.sendSetHeading(degrees)
    }

    private fun changedEnough(previous: Double, next: Double): Boolean {
        if (previous.isNaN() || next.isNaN()) return previous.isNaN() != next.isNaN()
        val diff = abs(((next - previous) % 360 + 540) % 360 - 180)
        return diff >= RESEND_THRESHOLD_DEGREES
    }

    private fun save(context: Context, t: Target?) {
        val editor = prefs(context).edit()
        if (t == null) {
            editor.clear()
        } else {
            // Doubles as raw bits - SharedPreferences has no putDouble.
            editor.putLong(KEY_LAT, t.latitude.toRawBits())
                .putLong(KEY_LNG, t.longitude.toRawBits())
                .putLong(KEY_UNTIL, t.untilMillis)
                .putBoolean(KEY_MANUAL, t.manual)
                .putString(KEY_LABEL, t.label)
        }
        editor.apply()
    }

    private fun load(context: Context): Target? {
        val prefs = prefs(context)
        if (!prefs.contains(KEY_UNTIL)) return null
        return Target(
            latitude = Double.fromBits(prefs.getLong(KEY_LAT, 0L)),
            longitude = Double.fromBits(prefs.getLong(KEY_LNG, 0L)),
            untilMillis = prefs.getLong(KEY_UNTIL, 0L),
            manual = prefs.getBoolean(KEY_MANUAL, false),
            label = prefs.getString(KEY_LABEL, null),
        )
    }

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** Initial great-circle bearing from (lat1, lng1) to (lat2, lng2), degrees 0-360 clockwise
     * from true north - the same north the firmware corrects its magnetometer to. */
    fun bearingDegrees(lat1: Double, lng1: Double, lat2: Double, lng2: Double): Double {
        val phi1 = Math.toRadians(lat1)
        val phi2 = Math.toRadians(lat2)
        val dLambda = Math.toRadians(lng2 - lng1)
        val y = sin(dLambda) * cos(phi2)
        val x = cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(dLambda)
        return (Math.toDegrees(atan2(y, x)) + 360) % 360
    }

    /** Haversine distance in metres. */
    fun distanceMeters(lat1: Double, lng1: Double, lat2: Double, lng2: Double): Double {
        val dPhi = Math.toRadians(lat2 - lat1)
        val dLambda = Math.toRadians(lng2 - lng1)
        val a = sin(dPhi / 2) * sin(dPhi / 2) +
            cos(Math.toRadians(lat1)) * cos(Math.toRadians(lat2)) * sin(dLambda / 2) * sin(dLambda / 2)
        return 2 * EARTH_RADIUS_METERS * asin(sqrt(a))
    }

    private const val EARTH_RADIUS_METERS = 6_371_000.0

    /** Tests only - this is process-wide state. */
    internal fun reset() = synchronized(lock) {
        loaded = true
        target = null
        lastSent = null
        _status.value = null
    }
}
