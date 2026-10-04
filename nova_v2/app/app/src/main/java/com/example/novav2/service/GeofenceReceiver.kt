package com.example.novav2.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.example.novav2.model.PlaceEvent
import com.example.novav2.state.GeofenceRegistrar
import com.example.novav2.state.ReminderEngine
import com.google.android.gms.location.Geofence
import com.google.android.gms.location.GeofenceStatusCodes
import com.google.android.gms.location.GeofencingEvent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * A place reminder's geofence - [GeofenceRegistrar] registers them. Manifest-registered, like
 * [ReminderAlarmReceiver], so it wakes a killed process; goAsync() because delivery reads Room.
 *
 * GEOFENCE_NOT_AVAILABLE means location was switched off and the platform has dropped every
 * geofence, so the registrar is told to re-add them all once it can.
 */
class GeofenceReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.applicationContext
        val event = GeofencingEvent.fromIntent(intent) ?: return
        if (event.hasError()) {
            Log.w(TAG, "geofence error ${GeofenceStatusCodes.getStatusCodeString(event.errorCode)}")
            if (event.errorCode == GeofenceStatusCodes.GEOFENCE_NOT_AVAILABLE) GeofenceRegistrar.markLost(app)
            return
        }
        val placeEvent = when (event.geofenceTransition) {
            Geofence.GEOFENCE_TRANSITION_DWELL, Geofence.GEOFENCE_TRANSITION_ENTER -> PlaceEvent.ARRIVE
            Geofence.GEOFENCE_TRANSITION_EXIT -> PlaceEvent.LEAVE
            else -> return
        }
        val hits = event.triggeringGeofences.orEmpty()
            .mapNotNull { GeofenceRegistrar.parseRequestId(it.requestId) }
        if (hits.isEmpty()) return

        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                ReminderEngine.onPlace(app, placeEvent, hits)
            } finally {
                pending.finish()
            }
        }
    }

    private companion object {
        const val TAG = "GeofenceReceiver"
    }
}
