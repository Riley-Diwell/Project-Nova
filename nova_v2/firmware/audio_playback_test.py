#!/usr/bin/env python3
"""
Decode raw ADPCM bytes captured from the ESP32 into a playable WAV.

Paste bytes as hex on stdin (whitespace / commas / 0x prefixes are all fine),
or point at a file, or pass --base64 if your BLE logger dumps base64.

  python adpcm_decode.py                    # paste hex, saves out.wav
  python adpcm_decode.py -i capture.hex     # read from file
  python adpcm_decode.py --base64           # paste base64 instead
  python adpcm_decode.py --play             # also play back (needs sounddevice)
"""

import argparse
import array
import base64
import sys
import wave

SAMPLE_RATE = 16000
BLOCK_SIZE = 260  # 4-byte header + 256 bytes of packed nibbles

# Standard IMA ADPCM tables — must match the encoder on the ESP32.
STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17,
    19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
    130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
    337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
    2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358,
    5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
    15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
]
INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8,
               -1, -1, -1, -1, 2, 4, 6, 8]


def decode_block(block):
    """Decode one 260-byte ADPCM block into a list of int16 samples."""
    predictor = int.from_bytes(block[0:2], 'little', signed=True)
    index = max(0, min(88, block[2]))

    samples = []
    for byte in block[4:]:
        # Low nibble is the earlier sample (matches the encoder's pack order).
        for nibble in (byte & 0x0F, (byte >> 4) & 0x0F):
            step = STEP_TABLE[index]
            diffq = step >> 3
            if nibble & 4: diffq += step
            if nibble & 2: diffq += step >> 1
            if nibble & 1: diffq += step >> 2

            predictor += -diffq if (nibble & 8) else diffq
            if predictor >  32767: predictor =  32767
            elif predictor < -32768: predictor = -32768

            index += INDEX_TABLE[nibble]
            if index < 0: index = 0
            elif index > 88: index = 88

            samples.append(predictor)
    return samples


def decode_stream(data):
    samples = []
    n_blocks = len(data) // BLOCK_SIZE
    for i in range(n_blocks):
        samples.extend(decode_block(data[i * BLOCK_SIZE:(i + 1) * BLOCK_SIZE]))
    leftover = len(data) - n_blocks * BLOCK_SIZE
    if leftover:
        print(f"[warn] {leftover} trailing bytes ignored (not a full 260-byte block)",
              file=sys.stderr)
    return samples, n_blocks


def parse_hex(text):
    cleaned = text.lower().replace('0x', '').replace(',', ' ')
    cleaned = ''.join(cleaned.split())  # strip all whitespace
    return bytes.fromhex(cleaned)


def parse_base64(text):
    return base64.b64decode(''.join(text.split()))


def write_wav(path, samples):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(array.array('h', samples).tobytes())


def main():
    ap = argparse.ArgumentParser(description="Decode ESP32 ADPCM stream to WAV")
    ap.add_argument('-i', '--input', help="Read encoded text from file instead of stdin")
    ap.add_argument('-o', '--output', default='out.wav',
                    help="Output WAV path (default: out.wav)")
    ap.add_argument('--base64', action='store_true', help="Input is base64, not hex")
    ap.add_argument('--play', action='store_true',
                    help="Play through speakers after saving (needs sounddevice)")
    args = ap.parse_args()

    if args.input:
        with open(args.input) as f:
            text = f.read()
    else:
        fmt = "base64" if args.base64 else "hex"
        print(f"Paste {fmt} bytes, then Ctrl-D (Ctrl-Z Enter on Windows):",
              file=sys.stderr)
        text = sys.stdin.read()

    data = parse_base64(text) if args.base64 else parse_hex(text)
    samples, n_blocks = decode_stream(data)
    dur = len(samples) / SAMPLE_RATE
    print(f"[info] {len(data)} bytes -> {n_blocks} blocks -> {len(samples)} samples "
          f"({dur:.2f}s at {SAMPLE_RATE} Hz)")

    write_wav(args.output, samples)
    print(f"[info] wrote {args.output}")

    if args.play:
        try:
            import numpy as np
            import sounddevice as sd
            sd.play(np.array(samples, dtype='int16'), SAMPLE_RATE)
            sd.wait()
        except ImportError:
            print("[warn] pip install sounddevice numpy  (needed for --play)",
                  file=sys.stderr)


if __name__ == '__main__':
    main()