package com.example.novav2.notes.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import com.example.novav2.ble.AdpcmDecoder
import com.example.novav2.ble.NovaBleProtocol
import java.io.DataInputStream
import java.io.EOFException
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Plays a kept recording (see [NoteAudioStore]) from a transcript timestamp. Decodes on the fly
 * from the stored ADPCM - each block is 32 ms and carries the decoder state in its header, so a
 * seek is "skip N blocks, seed, play".
 */
class NoteAudioPlayer(private val scope: CoroutineScope) {
    private var job: Job? = null
    private var track: AudioTrack? = null

    fun play(context: Context, noteId: String, fromSeconds: Double, onDone: () -> Unit = {}) {
        stop()
        val file = NoteAudioStore.fileFor(context, noteId)
        if (!file.exists()) return
        job = scope.launch(Dispatchers.IO) {
            try {
                playFile(file, fromSeconds)
            } finally {
                releaseTrack()
                launch(Dispatchers.Main) { onDone() }
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
        releaseTrack()
    }

    private fun CoroutineScope.playFile(file: File, fromSeconds: Double) {
        val rate = NovaBleProtocol.SAMPLE_RATE_HZ
        val skipBlocks = (fromSeconds * rate / NovaBleProtocol.SAMPLES_PER_BLOCK).toInt().coerceAtLeast(0)
        val minBuffer = AudioTrack.getMinBufferSize(rate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val audioTrack = AudioTrack.Builder()
            .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .setAudioFormat(AudioFormat.Builder().setSampleRate(rate)
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
            .setBufferSizeInBytes(minBuffer * 4)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        track = audioTrack
        audioTrack.play()

        val decoder = AdpcmDecoder()
        DataInputStream(file.inputStream().buffered()).use { input ->
            if (input.readInt() != NoteAudioStore.MAGIC) return
            var blockIndex = 0
            var seeded = false
            while (isActive) {
                val len = try { input.readUnsignedShort() } catch (_: EOFException) { break }
                val block = ByteArray(len)
                input.readFully(block)
                if (blockIndex++ < skipBlocks) continue
                if (!seeded) { decoder.seedFrom(block); seeded = true }
                val pcm = decoder.decodeBlock(block)
                audioTrack.write(pcm, 0, pcm.size)
            }
        }
    }

    private fun releaseTrack() {
        track?.let { runCatching { it.stop() }; it.release() }
        track = null
    }
}
