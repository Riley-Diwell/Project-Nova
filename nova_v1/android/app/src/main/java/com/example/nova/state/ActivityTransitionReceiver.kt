package com.example.nova.state

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.google.android.gms.location.ActivityRecognitionResult
import com.google.android.gms.location.DetectedActivity

/** Receives batched ActivityRecognition updates and stores the latest label in [ActivitySignal]. */
class ActivityTransitionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (!ActivityRecognitionResult.hasResult(intent)) return
        val result = ActivityRecognitionResult.extractResult(intent) ?: return
        ActivitySignal.latestActivity = describe(result.mostProbableActivity.type)
    }

    private fun describe(type: Int): String = when (type) {
        DetectedActivity.IN_VEHICLE -> "in_vehicle"
        DetectedActivity.ON_BICYCLE -> "on_bicycle"
        DetectedActivity.ON_FOOT -> "on_foot"
        DetectedActivity.RUNNING -> "running"
        DetectedActivity.STILL -> "still"
        DetectedActivity.TILTING -> "tilting"
        DetectedActivity.WALKING -> "walking"
        else -> "unknown"
    }
}
