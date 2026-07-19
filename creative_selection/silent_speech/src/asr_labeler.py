"""Offline speech-to-text labeler (Vosk) for auto-labeling training data.

The idea: when you speak OUT LOUD, the microphone + this ASR produces the text
(with per-word timestamps) that labels whatever your camera/EMG captured at the
same moment. Those auto-labeled (features -> word) pairs train the silent-speech
model for free. A simple energy VAD tells voiced (train) from silent (predict).

Fully offline / no account: uses a local Vosk model in models/.

    asr = ASR()
    text, words = asr.transcribe_wav("clip.wav")   # words: [{word,start,end}]
    # live:
    for words, voiced in asr.stream_words():        # generator over mic
        ...
"""
from __future__ import annotations

import json
import os
import queue
import wave
from math import gcd

import numpy as np
from vosk import Model, KaldiRecognizer

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models",
                         "vosk-model-small-en-us-0.15")
SR = 16000                # Vosk expects 16 kHz mono int16
VAD_RMS = 500.0           # int16 RMS threshold: above = voiced


def _read_wav_mono(path):
    with wave.open(path, "rb") as w:
        sr, n, ch, sw = (w.getframerate(), w.getnframes(),
                         w.getnchannels(), w.getsampwidth())
        raw = w.readframes(n)
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[sw]
    a = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sw == 1:            # unsigned 8-bit -> center
        a = (a - 128) * 256
    return a, sr


def _resample(a, sr_from, sr_to):
    if sr_from == sr_to:
        return a
    from scipy.signal import resample_poly
    g = gcd(sr_from, sr_to)
    return resample_poly(a, sr_to // g, sr_from // g)


def _to_pcm16(a):
    return np.clip(a, -32768, 32767).astype("<i2").tobytes()


class ASR:
    def __init__(self, model_path: str = MODEL_DIR):
        if not os.path.isdir(model_path):
            raise FileNotFoundError(f"Vosk model not found: {model_path}")
        self.model = Model(model_path)

    def _recognizer(self):
        r = KaldiRecognizer(self.model, SR)
        r.SetWords(True)          # enable per-word timestamps
        return r

    def transcribe_wav(self, path: str):
        """Return (text, [{'word','start','end','conf'}, ...]) for a wav file."""
        a, sr = _read_wav_mono(path)
        return self._decode(a, sr)

    def transcribe_array(self, samples, sr: int):
        """Transcribe an in-memory int16/float mono array. Returns (text, words)."""
        return self._decode(np.asarray(samples, dtype=np.float32).flatten(), sr)

    def _decode(self, a, sr):
        pcm = _to_pcm16(_resample(a, sr, SR))
        rec = self._recognizer()
        rec.AcceptWaveform(pcm)
        res = json.loads(rec.FinalResult())
        return res.get("text", ""), res.get("result", [])

    def stream_words(self, blocksize: int = 4000):
        """Yield (words, voiced) from the live microphone until interrupted.

        words is the per-utterance word list (with timestamps) when Vosk
        finalizes an utterance; voiced is a per-block energy VAD flag.
        """
        import sounddevice as sd
        q: "queue.Queue[bytes]" = queue.Queue()

        def cb(indata, frames, time_info, status):
            q.put(bytes(indata))

        rec = self._recognizer()
        with sd.RawInputStream(samplerate=SR, blocksize=blocksize, dtype="int16",
                               channels=1, callback=cb):
            while True:
                data = q.get()
                block = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                voiced = float(np.sqrt(np.mean(block ** 2))) > VAD_RMS
                if rec.AcceptWaveform(data):
                    res = json.loads(rec.Result())
                    yield res.get("result", []), voiced
                else:
                    yield None, voiced


class WhisperASR:
    """Whisper backend (faster-whisper) - much more accurate than Vosk-small,
    especially on short/isolated words. Fully offline after the one-time model
    download (no account). Same interface as ASR: transcribe_wav/array ->
    (text, words). Whisper doesn't emit reliable word timings for single words,
    so `words` here is just the cleaned tokens.
    """
    import re as _re
    _CLEAN = _re.compile(r"[^a-z0-9' ]")
    _DIGITS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
               "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}

    def __init__(self, model_size: str = "base.en", cpu_threads: int = 6):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8",
                                  cpu_threads=cpu_threads)

    @classmethod
    def _clean(cls, t: str) -> str:
        return cls._CLEAN.sub("", t.lower()).strip()

    def transcribe_array(self, samples, sr: int):
        a = np.asarray(samples, dtype=np.float32).flatten()
        if np.issubdtype(np.asarray(samples).dtype, np.integer):
            a = a / 32768.0
        a = _resample(a, sr, SR) if sr != SR else a
        segments, _ = self.model.transcribe(a, language="en", beam_size=5)
        text = self._clean(" ".join(s.text for s in segments))
        tokens = [self._DIGITS.get(w, w) for w in text.split()]
        text = " ".join(tokens)
        return text, [{"word": w} for w in tokens]

    def transcribe_words(self, samples, sr: int):
        """Return [{'word','start','end'}] with per-word times (for slicing)."""
        a = np.asarray(samples, dtype=np.float32).flatten()
        if np.issubdtype(np.asarray(samples).dtype, np.integer):
            a = a / 32768.0
        a = _resample(a, sr, SR) if sr != SR else a
        segments, _ = self.model.transcribe(a, language="en", beam_size=5,
                                            word_timestamps=True)
        out = []
        for s in segments:
            for w in (s.words or []):
                token = self._clean(w.word)
                if not token:
                    continue
                out.append({"word": self._DIGITS.get(token, token),
                            "start": float(w.start), "end": float(w.end)})
        return out

    def transcribe_wav(self, path: str):
        a, sr = _read_wav_mono(path)
        return self.transcribe_array(a.astype(np.int16), sr)


if __name__ == "__main__":
    import sys
    asr = ASR()
    if len(sys.argv) > 1:
        text, words = asr.transcribe_wav(sys.argv[1])
        print("TEXT:", text)
        for w in words:
            print(f"  {w['word']:<10} {w['start']:.2f}-{w['end']:.2f}s "
                  f"conf={w.get('conf', 0):.2f}")
    else:
        print("Pass a wav path to transcribe, or import ASR for live use.")
