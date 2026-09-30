package com.example.novav2.stt

import android.content.Context
import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.withTimeoutOrNull
import org.vosk.Model
import org.vosk.android.StorageService

private const val TAG = "VoskTranscriber"

/**
 * The Vosk model behind fully offline speech-to-text for the Nova device's BLE mic
 * stream - loading it, and holding it for [StreamingTranscriber], which feeds it one
 * recording at a time as the audio arrives. Deliberately not Android's SpeechRecognizer
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
    const val SAMPLE_RATE = 16000.0f

    /** Recorded with each note (CapturedNote.sttEngine) so a WER comparison
     * can tell which model produced a transcript. Keep in step with
     * build.gradle.kts' voskModelUrl. */
    const val ENGINE_NAME = "vosk-model-small-en-us-0.15"

    @Volatile private var model: Model? = null
    @Volatile private var loadFailed = false
    private val loaded = CompletableDeferred<Model?>()

    /** The loaded model, or null while it is still unpacking (or failed to). */
    val loadedModel: Model? get() = model

    /** Waits up to [timeoutMillis] for the model - a recording that ended before the model
     * finished unpacking is held and transcribed once it is ready, instead of dropped. */
    suspend fun awaitModel(timeoutMillis: Long): Model? =
        model ?: withTimeoutOrNull(timeoutMillis) { loaded.await() }

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
            { m ->
                model = m
                loaded.complete(m)
            },
            { e ->
                loadFailed = true
                loaded.complete(null)
                Log.e(TAG, "model unpack failed", e)
            },
        )
    }
}
