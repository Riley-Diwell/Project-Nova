package com.example.novav2.ble

/**
 * IMA ADPCM decoder — the exact inverse of the encoder in
 * nova_v2/firmware/ESP32-S3.ino/ESP32-S3.ino.ino (encodeADPCMSample /
 * encodeADPCMBlock). Same step/index tables — they must stay byte-for-byte
 * identical to the firmware's or predictor/step state silently drifts and the
 * decoded audio degrades into noise without either side raising an error.
 *
 * Stateful per utterance, not global: call [reset] when a stream's start flag
 * ([NovaBleProtocol.AUDIO_FLAG_START]) arrives. A second recording must not
 * inherit the first one's predictor — same reason firmware's resetADPCM()
 * runs at the start of every new recording, not once at boot.
 */
class AdpcmDecoder {
    private var predictor: Int = 0
    private var index: Int = 0

    fun reset() {
        predictor = 0
        index = 0
    }

    /**
     * Decodes one block: 4-byte header (predictor lo/hi, step index, reserved —
     * see encodeADPCMBlock) followed by packed 4-bit samples, low nibble first.
     * The header is only checked for presence (a short/malformed block decodes
     * to nothing rather than throwing); its predictor/index fields are not read
     * back — like the encoder, this decoder carries its own running state
     * across blocks rather than re-seeding from each block's header.
     *
     * Assumes an even sample count per block, true for every block the
     * firmware currently sends (always a full 512-sample buffer). The encoder
     * has a documented quirk for a trailing odd sample - it pads the last
     * nibble with 0 without actually encoding it - which this decoder does not
     * attempt to special-case, since it can't currently occur with a fixed
     * 260-byte block size.
     */
    fun decodeBlock(block: ByteArray): ShortArray {
        if (block.size <= 4) return ShortArray(0)
        val sampleCount = (block.size - 4) * 2
        val out = ShortArray(sampleCount)
        var outIdx = 0
        for (i in 4 until block.size) {
            val byte = block[i].toInt() and 0xFF
            out[outIdx++] = decodeSample(byte and 0x0F)
            if (outIdx < sampleCount) {
                out[outIdx++] = decodeSample((byte shr 4) and 0x0F)
            }
        }
        return out
    }

    private fun decodeSample(code: Int): Short {
        val step = STEP_TABLE[index]
        var diffq = step shr 3
        if (code and 4 != 0) diffq += step
        if (code and 2 != 0) diffq += step shr 1
        if (code and 1 != 0) diffq += step shr 2

        var pred = predictor + if (code and 8 != 0) -diffq else diffq
        pred = pred.coerceIn(-32768, 32767)
        predictor = pred

        index = (index + INDEX_TABLE[code]).coerceIn(0, STEP_TABLE.size - 1)
        return pred.toShort()
    }

    companion object {
        // Identical to firmware's adpcmStepTable/adpcmIndexTable.
        private val STEP_TABLE = intArrayOf(
            7, 8, 9, 10, 11, 12, 13, 14, 16, 17,
            19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
            50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
            130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
            337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
            876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
            2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358,
            5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
            15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767
        )
        private val INDEX_TABLE = intArrayOf(
            -1, -1, -1, -1, 2, 4, 6, 8,
            -1, -1, -1, -1, 2, 4, 6, 8
        )
    }
}
