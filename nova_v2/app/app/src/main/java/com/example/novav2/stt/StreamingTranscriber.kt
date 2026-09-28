package com.example.novav2.stt

import android.content.Context
import android.util.Log
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.EOFException
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer

private const val TAG = "StreamingTranscriber"

/**
 * Vosk, one [Recognizer] per recording, fed block by block.
 *
 * The old path ([VoskTranscriber.transcribe]) held a whole utterance's PCM in RAM and decoded
 * it in one pass after the button came up - 115 MB for an hour, and nothing at all if the model
 * was still unpacking. Here each 32 ms block goes straight into the recognizer, which emits a
 * segment every time it detects a pause (`acceptWaveForm` returns true), so memory stays flat
 * however long a capture runs and the transcript is nearly done by the time it stops.
 *
 * If the model has not loaded when audio starts arriving, the audio is spooled to a temp file
 * in the app's cache (never uploaded) and replayed into the recognizer once the model is ready.
 */
class StreamingTranscriber(private val context: Context) : Transcriber {
    override fun newSession(): TranscriberSession = VoskSession(context.applicationContext)
}

private class VoskSession(private val context: Context) : TranscriberSession {
    private var recognizer: Recognizer? = null
    private var spool: File? = null
    private var spoolOut: DataOutputStream? = null
    private var samplesSeen = 0L
    private val segments = mutableListOf<TranscriptSegment>()
    private val confidences = mutableListOf<Double>()

    override fun accept(samples: ShortArray) {
        samplesSeen += samples.size
        val rec = recognizer ?: VoskTranscriber.loadedModel?.let { startRecognizer(it) }
        if (rec == null) {
            spoolToDisk(samples)
            return
        }
        feed(rec, samples)
    }

    override suspend fun finish(): TranscriptResult {
        var rec = recognizer
        if (rec == null) {
            val model = VoskTranscriber.awaitModel(MODEL_WAIT_MS)
            if (model == null) {
                Log.w(TAG, "model never loaded - recording kept only until now, transcript empty")
                cancel()
                return result()
            }
            rec = startRecognizer(model)
        }
        try {
            collect(rec.finalResult)
        } finally {
            rec.close()
            recognizer = null
        }
        return result()
    }

    override fun cancel() {
        recognizer?.close()
        recognizer = null
        closeSpool(delete = true)
    }

    private fun startRecognizer(model: Model): Recognizer {
        val rec = Recognizer(model, VoskTranscriber.SAMPLE_RATE).apply { setWords(true) }
        recognizer = rec
        drainSpool(rec)
        return rec
    }

    private fun feed(rec: Recognizer, samples: ShortArray) {
        try {
            if (rec.acceptWaveForm(samples, samples.size)) collect(rec.result)
        } catch (e: Exception) {
            Log.e(TAG, "recognizer rejected a block", e)
        }
    }

    /** One Vosk result JSON -> one segment. Times come from the word timings (setWords). */
    private fun collect(json: String) {
        val obj = JSONObject(json)
        val text = obj.optString("text").trim()
        if (text.isEmpty()) return
        val words = obj.optJSONArray("result")
        var start = Double.NaN
        var end = Double.NaN
        if (words != null && words.length() > 0) {
            start = words.getJSONObject(0).optDouble("start")
            end = words.getJSONObject(words.length() - 1).optDouble("end")
            for (i in 0 until words.length()) {
                val conf = words.getJSONObject(i).optDouble("conf")
                if (!conf.isNaN()) confidences += conf
            }
        }
        val now = samplesSeen / VoskTranscriber.SAMPLE_RATE.toDouble()
        segments += TranscriptSegment(
            startS = if (start.isNaN()) segments.lastOrNull()?.endS ?: 0.0 else start,
            endS = if (end.isNaN()) now else end,
            text = text,
        )
    }

    private fun result() = TranscriptResult(
        text = segments.joinToString(" ") { it.text },
        segments = segments.toList(),
        avgConfidence = confidences.takeIf { it.isNotEmpty() }?.average(),
        engine = VoskTranscriber.ENGINE_NAME,
        durationS = samplesSeen / VoskTranscriber.SAMPLE_RATE.toDouble(),
    )

    // --- spooling while the model loads -------------------------------------------------

    private fun spoolToDisk(samples: ShortArray) {
        val out = spoolOut ?: run {
            val file = File.createTempFile("nova-stt-", ".pcm", context.cacheDir)
            spool = file
            DataOutputStream(BufferedOutputStream(FileOutputStream(file))).also { spoolOut = it }
        }
        out.writeInt(samples.size)
        for (s in samples) out.writeShort(s.toInt())
    }

    private fun drainSpool(rec: Recognizer) {
        val file = spool ?: return
        spoolOut?.close()
        spoolOut = null
        try {
            DataInputStream(FileInputStream(file).buffered()).use { input ->
                while (true) {
                    val n = try { input.readInt() } catch (_: EOFException) { break }
                    val block = ShortArray(n) { input.readShort() }
                    feed(rec, block)
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "spooled audio unreadable", e)
        } finally {
            closeSpool(delete = true)
        }
    }

    private fun closeSpool(delete: Boolean) {
        try { spoolOut?.close() } catch (_: Exception) {}
        spoolOut = null
        if (delete) spool?.delete()
        spool = null
    }

    private companion object {
        // First-run model unpack can take a while; a note is worth waiting for.
        const val MODEL_WAIT_MS = 60_000L
    }
}
