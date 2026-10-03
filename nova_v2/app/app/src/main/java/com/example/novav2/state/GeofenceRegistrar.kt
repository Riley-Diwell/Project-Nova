package com.example.novav2.state

import android.Manifest
import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.core.content.ContextCompat
import com.example.novav2.data.NovaDatabase
import com.example.novav2.data.ReminderEntity
import com.example.novav2.model.PlaceEvent
import com.example.novav2.service.GeofenceReceiver
import com.google.android.gms.common.api.ApiException
import com.google.android.gms.location.Geofence
import com.google.android.gms.location.GeofenceStatusCodes
import com.google.android.gms.location.GeofencingRequest
import com.google.android.gms.location.LocationServices
import com.google.android.gms.tasks.Task
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlin.coroutines.resume

/**
 * Keeps the platform's geofences matching the place reminders in the table - the place
 * counterpart of [ReminderScheduler]'s one alarm, and called from its reconcile, so every change
 * that reconciles the alarm reconciles these too.
 *
 * One geofence per circle of each armed reminder ([com.example.novav2.data.ReminderDao.armedAtPlaces]):
 * "arrive" is a DWELL of [LOITER_MILLIS], so driving past the shops doesn't count as going to
 * them, and "leave" is an EXIT. No initial trigger - "remind me when I get home", said at home,
 * means the next time.
 *
 * Incremental, not remove-all-and-re-add: re-adding a geofence the user is standing in resets
 * it, which would lose a dwell in progress (and every reminder change reconciles). The request
 * ids registered are kept in prefs - geofences outlive the process, so a cold start trusts them -
 * and each id encodes its circle and event, so a moved place is a new id. The platform drops
 * every geofence on reboot, on location being switched off, and when Play services' data is
 * cleared: [markLost] (from [GeofenceReceiver]'s GEOFENCE_NOT_AVAILABLE, and
 * [com.example.novav2.service.ReminderRescheduleReceiver] on boot and update) forgets the stored
 * ids, so the next reconcile clears whatever is left and re-adds everything. A reconcile that
 * failed is retried from SignalMonitorService's tick ([retryIfFailed]).
 *
 * Android allows [MAX_GEOFENCES] per app; past that the circles nearest the user's last known
 * location win.
 *
 * Needs ACCESS_FINE_LOCATION, and ACCESS_BACKGROUND_LOCATION on API 29+ ("Allow all the time"),
 * which Android only grants from Settings - [status] drives the Reminders screen's banner.
 */
object GeofenceRegistrar {
    private const val TAG = "GeofenceRegistrar"
    private const val REQUEST_CODE = 400
    private const val PREFS = "nova_geofences"
    private const val KEY_REGISTERED = "registered"

    const val MAX_GEOFENCES = 100
    private const val LOITER_MILLIS = 2 * 60_000
    /** How late the platform may report a transition, to save battery. A reminder a minute late
     * at the shops is fine. */
    private const val RESPONSIVENESS_MILLIS = 60_000

    enum class Status {
        /** No place reminders, or all registered. */
        OK,
        /** Place reminders exist but location isn't allowed all the time. */
        NEEDS_BACKGROUND_LOCATION,
        /** Location is switched off, or the platform refused - retried on the next tick. */
        UNAVAILABLE,
    }

    private val _status = MutableStateFlow(Status.OK)
    val status: StateFlow<Status> = _status

    private val mutex = Mutex()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    @Volatile private var failed = false

    fun hasPermission(context: Context): Boolean {
        val fine = ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
        val background = Build.VERSION.SDK_INT < Build.VERSION_CODES.Q ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_BACKGROUND_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
        return fine && background
    }

    /** "reminderId|index|event|lat,lng,radius" - see the class comment. */
    fun requestId(reminderId: String, index: Int, event: PlaceEvent, lat: Double, lng: Double, radius: Float): String =
        "$reminderId|$index|${event.wire}|${"%.6f".format(java.util.Locale.US, lat)},${"%.6f".format(java.util.Locale.US, lng)},${radius.toInt()}"

    /** The reminder and circle index a request id names, or null if it isn't one of ours. */
    fun parseRequestId(requestId: String): Pair<String, Int>? {
        val parts = requestId.split("|")
        if (parts.size != 4) return null
        return parts[0] to (parts[1].toIntOrNull() ?: return null)
    }

    /** The platform has dropped them all - the next reconcile re-adds everything. */
    fun markLost(context: Context) {
        prefs(context).edit().remove(KEY_REGISTERED).apply()
    }

    /** From SignalMonitorService's tick: a no-op unless the last reconcile failed. */
    fun retryIfFailed(context: Context) {
        if (!failed) return
        val app = context.applicationContext
        scope.launch { reconcile(app) }
    }

    @SuppressLint("MissingPermission")
    suspend fun reconcile(context: Context) = mutex.withLock {
        val app = context.applicationContext
        val armed = NovaDatabase.getInstance(app).reminderDao().armedAtPlaces()
        val desired = nearestFirst(geofencesFor(armed)).take(MAX_GEOFENCES).associateBy { it.requestId }

        if (!hasPermission(app)) {
            // Nothing can be registered, and the platform drops what was when permission goes.
            // Granting it is the retry (the Reminders screen reconciles on resume).
            markLost(app)
            failed = false
            _status.value = if (desired.isEmpty()) Status.OK else Status.NEEDS_BACKGROUND_LOCATION
            return@withLock
        }

        val prefs = prefs(app)
        val registered = prefs.getStringSet(KEY_REGISTERED, null)?.toSet()
        val client = LocationServices.getGeofencingClient(app)

        try {
            val toAdd = if (registered == null) {
                // Lost: clear any strays under old ids, then add everything.
                client.removeGeofences(pendingIntent(app)).awaitResult()
                desired.values.toList()
            } else {
                val toRemove = registered - desired.keys
                if (toRemove.isNotEmpty()) client.removeGeofences(toRemove.toList()).awaitResult()
                desired.values.filter { it.requestId !in registered }
            }
            if (toAdd.isNotEmpty()) {
                val request = GeofencingRequest.Builder()
                    .setInitialTrigger(0)
                    .addGeofences(toAdd)
                    .build()
                client.addGeofences(request, pendingIntent(app)).awaitResult()
            }
            prefs.edit().putStringSet(KEY_REGISTERED, desired.keys).apply()
            failed = false
            _status.value = Status.OK
        } catch (e: Exception) {
            val code = (e as? ApiException)?.statusCode
            Log.w(TAG, "geofences not registered (${code?.let(GeofenceStatusCodes::getStatusCodeString) ?: e}")
            markLost(app)
            failed = desired.isNotEmpty()
            _status.value = when (code) {
                GeofenceStatusCodes.GEOFENCE_INSUFFICIENT_LOCATION_PERMISSION -> Status.NEEDS_BACKGROUND_LOCATION
                else -> if (desired.isEmpty()) Status.OK else Status.UNAVAILABLE
            }
        }
    }

    private fun geofencesFor(armed: List<ReminderEntity>): List<Geofence> = armed.flatMap { r ->
        val place = r.place ?: return@flatMap emptyList()
        place.points.mapIndexed { i, point ->
            val builder = Geofence.Builder()
                .setRequestId(requestId(r.id, i, place.on, point.lat, point.lng, point.radiusMeters))
                .setCircularRegion(point.lat, point.lng, point.radiusMeters)
                .setExpirationDuration(Geofence.NEVER_EXPIRE)
                .setNotificationResponsiveness(RESPONSIVENESS_MILLIS)
            when (place.on) {
                PlaceEvent.ARRIVE -> builder
                    .setTransitionTypes(Geofence.GEOFENCE_TRANSITION_DWELL)
                    .setLoiteringDelay(LOITER_MILLIS)
                PlaceEvent.LEAVE -> builder.setTransitionTypes(Geofence.GEOFENCE_TRANSITION_EXIT)
            }
            builder.build()
        }
    }

    /** Nearest the user's last fix first, so the [MAX_GEOFENCES] kept are the ones that matter
     * soonest. Unchanged order when there's no fix. */
    private fun nearestFirst(fences: List<Geofence>): List<Geofence> {
        if (fences.size <= MAX_GEOFENCES) return fences
        val here = LocationSignal.latestLocationCtx?.split(",")?.mapNotNull { it.toDoubleOrNull() }
            ?.takeIf { it.size == 2 } ?: return fences
        val result = FloatArray(1)
        return fences.sortedBy {
            android.location.Location.distanceBetween(here[0], here[1], it.latitude, it.longitude, result)
            result[0]
        }
    }

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** Mutable: the platform fills in the GeofencingEvent's extras. */
    private fun pendingIntent(context: Context): PendingIntent {
        val mutable = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0
        return PendingIntent.getBroadcast(
            context, REQUEST_CODE,
            Intent(context, GeofenceReceiver::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or mutable,
        )
    }

    private suspend fun <T> Task<T>.awaitResult(): T = suspendCancellableCoroutine { continuation ->
        addOnSuccessListener { continuation.resume(it) }
        addOnFailureListener { continuation.resumeWith(Result.failure(it)) }
        addOnCanceledListener { continuation.cancel() }
    }
}
