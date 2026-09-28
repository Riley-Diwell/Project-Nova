package com.example.novav2.ble

/** What kind of recording this is - from the START frame's flags (docs/ble-protocol.md). */
enum class RecordingMode {
    /** A command or question for the assistant. */
    HOLD,
    /** A dictated note. */
    NOTE;

    companion object {
        fun fromStartFlags(flags: Int): RecordingMode = when {
            flags and NovaBleProtocol.AUDIO_FLAG_NOTE != 0 -> NOTE
            else -> HOLD
        }
    }
}

/**
 * One ordered stream of what the device's audio characteristic says, a recording at a time:
 * `Start`, any number of `Pcm`, then exactly one `End`. Replaces the separate
 * audioChunks/utteranceActive/completedUtterances flows, which had no ordering guarantee
 * between them - a collector of this one flow always sees a
 * recording's frames in the order they arrived.
 */
sealed class AudioFrame {
    data class Start(val mode: RecordingMode, val startedAtMillis: Long) : AudioFrame()

    /** One decoded block. [adpcm] is the raw block as sent (4-byte header + nibbles), kept so an
     * opted-in capture can be stored compressed on the phone (NoteAudioStore). */
    class Pcm(val samples: ShortArray, val adpcm: ByteArray) : AudioFrame()

    /** [truncated]: the phone gave up waiting for frames (the link
     * dropped, or the END frame was lost) - whatever arrived is still a usable recording. */
    data class End(val truncated: Boolean) : AudioFrame()
}

/**
 * Turns raw audio-characteristic notifications into [AudioFrame]s. Pure Kotlin with an
 * injected clock, so the sequencing rules are JVM-testable without Bluetooth.
 *
 * Two guards against a recording being lost or never ending:
 *  - A START while a recording is already open is ignored. Old firmware flagged START
 *    whenever its 8-bit sequence number wrapped (every ~8.2 s), and the phone used to reset
 *    on it - discarding everything recorded so far.
 *  - [checkInactivity] closes a recording that has had no frame for [inactivityTimeoutMillis],
 *    marked truncated, so a lost END frame or a dropped link still produces a recording.
 *
 * Not thread-safe on its own; [NovaGattClient] serialises calls.
 */
class AudioFrameAssembler(
    private val emit: (AudioFrame) -> Unit,
    private val clock: () -> Long = System::currentTimeMillis,
    val inactivityTimeoutMillis: Long = 1_500L,
    private val onSequenceGap: (expected: Int, got: Int) -> Unit = { _, _ -> },
) {
    private val decoder = AdpcmDecoder()
    private var expectedSeq: Int? = null
    private var lastFrameAtMillis: Long = 0L

    var active: Boolean = false
        private set

    fun onFrame(frame: ByteArray) {
        if (frame.size < 2) return
        val seq = frame[0].toInt() and 0xFF
        val flags = frame[1].toInt() and 0xFF
        val block = frame.copyOfRange(2, frame.size)
        lastFrameAtMillis = clock()

        if (flags and NovaBleProtocol.AUDIO_FLAG_START != 0 && !active) {
            decoder.reset()
            expectedSeq = seq
            active = true
            emit(AudioFrame.Start(RecordingMode.fromStartFlags(flags), lastFrameAtMillis))
        }

        if (!active) {
            // Mid-recording frames after we already closed it (a late frame after the
            // inactivity finaliser fired, or a connection that joined mid-stream). There is no
            // START to decode from - the ADPCM predictor would be wrong - so drop them until the
            // next START rather than emitting noise as a recording.
            return
        }

        val expected = expectedSeq
        if (expected != null && expected != seq) onSequenceGap(expected, seq)
        expectedSeq = (seq + 1) and 0xFF

        if (block.isNotEmpty()) {
            val pcm = decoder.decodeBlock(block)
            if (pcm.isNotEmpty()) emit(AudioFrame.Pcm(pcm, block))
        }

        if (flags and NovaBleProtocol.AUDIO_FLAG_END != 0) {
            finish(truncated = false)
        }
    }

    /** Closes the open recording if nothing has arrived for [inactivityTimeoutMillis]. Returns
     * true if it did. Call periodically, and on disconnect via [forceEnd]. */
    fun checkInactivity(): Boolean {
        if (!active || clock() - lastFrameAtMillis < inactivityTimeoutMillis) return false
        finish(truncated = true)
        return true
    }

    /** The link is gone: close whatever is open, marked truncated. */
    fun forceEnd() {
        if (active) finish(truncated = true)
    }

    private fun finish(truncated: Boolean) {
        active = false
        expectedSeq = null
        emit(AudioFrame.End(truncated))
    }
}
