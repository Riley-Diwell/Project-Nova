package com.example.novav2.stt

import android.content.Context
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.StorageService

private const val TAG = "VoskTranscriber"

/**
 * Fully offline speech-to-text for audio the app already has in hand - the Nova
 * device's BLE mic stream, reassembled by [com.example.novav2.ble.NovaGattClient]
 * into one PCM16 buffer per utterance. Deliberately not Android's SpeechRecognizer
 * (used elsewhere in this app for the phone's own mic): that class always records
 * from its own live AudioRecord session and has no public API to accept a
 * pre-recorded buffer instead, which is exactly what BLE audio is by the time it
 * reaches here.
 *
 * Model files ship as an app asset (see app/build.gradle.kts' downloadVoskModel
 * task) and are unpacked to external storage once via [StorageService.unpack] -
 * everything after that runs on-device with no network call, matching NOVA's
 * eventual local-first direction (ADR-0001) more closely than a cloud STT call
 * would have.
 */
object VoskTranscriber {
    /** Must match app/build.gradle.kts' voskModelAssetName - the asset folder
     * StorageService.unpack looks for. */
    private const val MODEL_ASSET_PATH = "model-en-us"

    /** Must match the device mic's I2S sample rate (see ESP32-S3.ino.ino's
     * i2s_install) - Recognizer accuracy silently degrades if this drifts from
     * the audio's actual rate rather than erroring. */
    private const val SAMPLE_RATE = 16000.0f

    @Volatile private var model: Model? = null
    @Volatile private var loadFailed = false

    /** Starts unpacking the model in the background so it's ready before the first
     * utterance arrives. Safe to call more than once - StorageService.unpack is
     * itself idempotent (skips re-copying once the target's uuid file already
     * matches), and this additionally no-ops once a model is loaded or a load has
     * already failed. */
    fun preload(context: Context) {
        if (model != null || loadFailed) return
        StorageService.unpack(
            context.applicationContext,
            MODEL_ASSET_PATH,
            "model",
            { loaded -> model = loaded },
            { e ->
                loadFailed = true
                Log.e(TAG, "model unpack failed", e)
            },
        )
    }

    /**
     * Transcribes one complete utterance. Returns null if the model isn't ready
     * yet, decoding failed, or nothing intelligible was heard - best-effort, same
     * stance as every other device/network touch in this codebase (e.g.
     * [com.example.novav2.ble.NovaDeviceRepository]'s own doc comment): a missed
     * utterance is dropped silently rather than surfaced as an error, since there
     * is no UI here to show one to.
     */
    suspend fun transcribe(pcm16le: ByteArray): String? = withContext(Dispatchers.Default) {
        val loadedModel = model ?: run {
            Log.w(TAG, "transcribe called before model finished loading - dropping utterance")
            return@withContext null
        }
        try {
            Recognizer(loadedModel, SAMPLE_RATE).use { recognizer ->
                recognizer.acceptWaveForm(pcm16le, pcm16le.size)
                JSONObject(recognizer.finalResult).optString("text").takeIf { it.isNotBlank() }
            }
        } catch (e: Exception) {
            Log.e(TAG, "transcription failed", e)
            null
        }
    }
}
