package com.example.nova.state

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Build
import kotlin.math.abs
import kotlin.math.sqrt

/**
 * Sensor-inference signals (CONTEXT.md "User State" - ambient sensor inference): motion
 * (still/moving), ambient light (lux), proximity (near/far), screen orientation
 * (face_up/face_down/portrait/landscape), and cumulative step count since last device reboot.
 * No runtime permission needed - a device missing a given sensor leaves that field null.
 * Bracket collection with [startUpdates]/[stopUpdates].
 */
object SensorSignal : SensorEventListener {
    private const val STILL_VARIANCE_THRESHOLD = 0.35f
    private const val WINDOW_SIZE = 15
    private const val FACE_THRESHOLD = 7.0f

    @Volatile var motion: String? = null
        private set
    @Volatile var ambientLightLux: Float? = null
        private set
    @Volatile var proximityNear: Boolean? = null
        private set
    @Volatile var screenOrientation: String? = null
        private set
    @Volatile var stepCountSinceBoot: Int? = null
        private set

    private var registered = false
    private val recentDeviations = ArrayDeque<Float>()

    fun startUpdates(context: Context) {
        if (registered) return
        val sensorManager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager

        listOf(Sensor.TYPE_ACCELEROMETER, Sensor.TYPE_LIGHT, Sensor.TYPE_PROXIMITY).forEach { type ->
            sensorManager.getDefaultSensor(type)?.let {
                sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_NORMAL)
            }
        }

        // TYPE_STEP_COUNTER requires ACTIVITY_RECOGNITION on API 29+; skip if not granted.
        if (ActivitySignal.hasPermission(context)) {
            sensorManager.getDefaultSensor(Sensor.TYPE_STEP_COUNTER)?.let {
                sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_NORMAL)
            }
        }

        registered = true
    }

    fun stopUpdates(context: Context) {
        if (!registered) return
        val sensorManager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
        sensorManager.unregisterListener(this)
        registered = false
    }

    override fun onSensorChanged(event: SensorEvent) {
        when (event.sensor.type) {
            Sensor.TYPE_ACCELEROMETER -> {
                val (x, y, z) = event.values
                val deviation = sqrt(x * x + y * y + z * z) - SensorManager.GRAVITY_EARTH
                if (recentDeviations.size == WINDOW_SIZE) recentDeviations.removeFirst()
                recentDeviations.addLast(deviation)
                val variance = recentDeviations.map { it * it }.average().toFloat()
                motion = if (variance < STILL_VARIANCE_THRESHOLD) "still" else "moving"
                screenOrientation = when {
                    abs(z) > FACE_THRESHOLD -> if (z > 0) "face_up" else "face_down"
                    abs(y) >= abs(x) -> "portrait"
                    else -> "landscape"
                }
            }
            Sensor.TYPE_LIGHT -> ambientLightLux = event.values[0]
            Sensor.TYPE_PROXIMITY -> proximityNear = event.values[0] < event.sensor.maximumRange
            Sensor.TYPE_STEP_COUNTER -> stepCountSinceBoot = event.values[0].toInt()
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
}
