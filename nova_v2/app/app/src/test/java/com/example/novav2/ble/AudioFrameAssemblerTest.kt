package com.example.novav2.ble

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Frame reassembly across the sequence-number wrap, ordering,
 * start flags, and the inactivity finaliser. */
class AudioFrameAssemblerTest {
    private var now = 0L
    private val out = mutableListOf<AudioFrame>()
    private val assembler = AudioFrameAssembler(emit = { out += it }, clock = { now })

    private fun frame(seq: Int, flags: Int, block: Boolean = true): ByteArray {
        val adpcm = if (block) ByteArray(260).also { it[4] = 0x12 } else ByteArray(0)
        return byteArrayOf(seq.toByte(), flags.toByte()) + adpcm
    }

    private fun feedRecording(frames: Int, startFlags: Int = NovaBleProtocol.AUDIO_FLAG_START, endFlags: Int = 0,
                              oldFirmware: Boolean = false) {
        for (i in 0 until frames) {
            val seq = i and 0xFF
            // Old firmware flagged START whenever its 8-bit sequence wrapped to 0.
            val flags = if (i == 0) startFlags
                else if (oldFirmware && seq == 0) NovaBleProtocol.AUDIO_FLAG_START else 0
            assembler.onFrame(frame(seq, flags))
            now += 32
        }
        assembler.onFrame(frame(frames and 0xFF, NovaBleProtocol.AUDIO_FLAG_END or endFlags, block = false))
    }

    @Test
    fun `600 frames across the sequence wrap make exactly one recording`() {
        feedRecording(600, oldFirmware = true)
        assertEquals(1, out.count { it is AudioFrame.Start })
        assertEquals(600, out.count { it is AudioFrame.Pcm })
        assertEquals(1, out.count { it is AudioFrame.End })
        assertEquals(600 * 512, out.filterIsInstance<AudioFrame.Pcm>().sumOf { it.samples.size })
    }

    @Test
    fun `frames come out in order - start, pcm, end`() {
        feedRecording(10)
        feedRecording(5)
        val shape = out.map { when (it) { is AudioFrame.Start -> 'S'; is AudioFrame.Pcm -> 'p'; is AudioFrame.End -> 'E' } }
            .joinToString("")
        assertEquals("S" + "p".repeat(10) + "E" + "S" + "p".repeat(5) + "E", shape)
    }

    @Test
    fun `start flags pick the recording mode`() {
        feedRecording(1, startFlags = NovaBleProtocol.AUDIO_FLAG_START)
        feedRecording(1, startFlags = NovaBleProtocol.AUDIO_FLAG_START or NovaBleProtocol.AUDIO_FLAG_NOTE)
        // A bit the phone doesn't know is ignored - still a hold.
        feedRecording(1, startFlags = NovaBleProtocol.AUDIO_FLAG_START or 0x08)
        assertEquals(
            listOf(RecordingMode.HOLD, RecordingMode.NOTE, RecordingMode.HOLD),
            out.filterIsInstance<AudioFrame.Start>().map { it.mode },
        )
    }

    @Test
    fun `a recording with no end frame is closed by the inactivity finaliser`() {
        assembler.onFrame(frame(0, NovaBleProtocol.AUDIO_FLAG_START))
        assembler.onFrame(frame(1, 0))
        now += 1_000
        assertEquals(false, assembler.checkInactivity())
        now += 600
        assertTrue(assembler.checkInactivity())
        assertEquals(AudioFrame.End(truncated = true), out.last())
        assertEquals(false, assembler.active)
    }

    @Test
    fun `frames after a recording closed are dropped until the next start`() {
        assembler.onFrame(frame(7, 0))
        assertTrue(out.isEmpty())
    }

    @Test
    fun `force end on disconnect closes an open recording once`() {
        assembler.onFrame(frame(0, NovaBleProtocol.AUDIO_FLAG_START))
        assembler.forceEnd()
        assembler.forceEnd()
        assertEquals(1, out.count { it is AudioFrame.End })
    }

    @Test
    fun `seeding from a block header resumes mid-stream decode`() {
        val first = ByteArray(260).also { for (i in 4 until 260) it[i] = 0x77 }
        // A second decoder seeded from a header carrying the first's end state decodes the
        // next block identically to one that ran from the start.
        val continuous = AdpcmDecoder().apply { decodeBlock(first) }
        val next = ByteArray(260).also { for (i in 4 until 260) it[i] = 0x31 }
        val expected = continuous.decodeBlock(next.copyOf())
        val header = headerOf(AdpcmDecoder().apply { decodeBlock(first) })
        val seeded = AdpcmDecoder().apply { seedFrom(header + next.copyOfRange(4, 260)) }
        assertTrue(expected.contentEquals(seeded.decodeBlock(header + next.copyOfRange(4, 260))))
    }

    /** The 4-byte header the firmware would write for a decoder in this state. */
    private fun headerOf(d: AdpcmDecoder): ByteArray {
        val predictor = AdpcmDecoder::class.java.getDeclaredField("predictor").apply { isAccessible = true }.getInt(d)
        val index = AdpcmDecoder::class.java.getDeclaredField("index").apply { isAccessible = true }.getInt(d)
        return byteArrayOf((predictor and 0xFF).toByte(), ((predictor shr 8) and 0xFF).toByte(), index.toByte(), 0)
    }
}
