"""Talk-to-teach / mouth-to-predict: self-supervised silent-speech training.

Press SPACE and either:
  * SPEAK a word OUT LOUD  -> the mic hears it, offline ASR (Vosk) transcribes
    it, and that word auto-labels the lip motion captured at the same time. The
    sample is saved and the model RETRAINS. (You are teaching it by talking.)
  * MOUTH a word SILENTLY  -> no voice detected, so it PREDICTS the word from
    lip motion using what it has learned.

A simple audio-energy VAD decides which mode you're in automatically. This is
the auto-labeling idea: vocalized speech supervises the silent-speech model.

Controls:  SPACE = record a ~1.5s window    ESC = quit

Note: with the tiny SVM, brand-new words start weak (few samples); repeat a word
a few times out loud to reinforce it. All samples land in data/ and blend with
prior recordings.
"""
from __future__ import annotations

import os
import time

import cv2
import numpy as np
import sounddevice as sd

from signal_source import WebcamLipSource
from kalman import KalmanSmoother
from decoder import DigitDecoder, train_and_save
from asr_labeler import WhisperASR
import record_session

DURATION = 2.0       # a bit longer so words aren't clipped
SR = 16000
VOICED_RMS = 350.0   # int16 RMS above this = "you spoke out loud"
DEBUG_WAV = os.path.join(record_session.DATA_DIR, "_last_teach.wav")


def _save_wav(path, samples_int16, sr=SR):
    """Dump the last teach-window audio so capture quality can be inspected."""
    import wave
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.asarray(samples_int16, dtype="<i2").tobytes())


def _draw(source, lines, bars=None):
    frame = getattr(source, "last_frame", None)
    frame = np.zeros((480, 640, 3), np.uint8) if frame is None else frame.copy()
    y = 40
    for text, col, scale in lines:
        cv2.putText(frame, text, (18, y), cv2.FONT_HERSHEY_SIMPLEX, scale, col, 2)
        y += int(34 * scale) + 8
    if bars:
        by = y + 5
        for label, prob in bars:
            w = int(prob * 240)
            cv2.rectangle(frame, (150, by - 14), (150 + w, by), (0, 200, 0), -1)
            cv2.putText(frame, label, (18, by), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (255, 255, 255), 1)
            by += 25
    cv2.imshow("Silent Speech - Talk to Teach", frame)


def type_word(source, prefill=""):
    """Free-text entry via the preview window. Returns the word or None."""
    buf = prefill
    while True:
        source.read()
        _draw(source, [("type the correct word:", (0, 255, 255), 0.8),
                       (buf + "_", (255, 255, 255), 1.2),
                       ("Enter = save   Backspace = del   Esc = cancel",
                        (180, 180, 180), 0.55)])
        k = cv2.waitKey(1) & 0xFF
        if k in (13, 10):
            return buf.strip().lower() or None
        if k == 27:
            return None
        if k == 8:
            buf = buf[:-1]
        elif k == 32:
            buf += " "
        elif 32 < k < 127:
            buf += chr(k)


def review_asr(source, heard):
    """Confirm/fix the ASR result. Returns (action, label).

    action: 'accept' (label valid) | 'redo' (re-record) | 'discard'.
    """
    while True:
        source.read()
        if heard:
            lines = [(f"heard: '{heard}'", (0, 255, 255), 1.1),
                     ("ENTER = correct   T = type fix", (255, 255, 255), 0.6),
                     ("R = redo   X = discard", (255, 255, 255), 0.6)]
        else:
            lines = [("didn't catch a word", (0, 0, 255), 0.9),
                     ("T = type it   R = redo   X = discard",
                      (255, 255, 255), 0.6)]
        _draw(source, lines)
        k = cv2.waitKey(1) & 0xFF
        if heard and k in (13, 10):
            return "accept", heard
        if k in (ord("t"), ord("T")):
            typed = type_word(source, heard)
            if typed:
                return "accept", typed
        elif k in (ord("r"), ord("R")):
            return "redo", None
        elif k in (ord("x"), ord("X"), 27):
            return "discard", None


def record_window(source):
    """Capture camera features AND mic audio over the same ~1.5s window."""
    audio = sd.rec(int(DURATION * SR), samplerate=SR, channels=1, dtype="int16")
    raw, times = [], []
    t0 = time.time()
    while time.time() - t0 < DURATION:
        feat = source.read()
        if feat is not None:
            raw.append(feat)
            times.append(time.time() - t0)
        _draw(source, [(f"REC  {time.time()-t0:0.1f}/{DURATION:0.1f}s",
                        (0, 0, 255), 1.1)])
        cv2.waitKey(1)
    sd.wait()
    return np.array(raw), np.array(times), audio.flatten()


def main():
    source = WebcamLipSource()
    asr = WhisperASR()
    source.open()
    ks = KalmanSmoother(source.n_features, dt=1.0, q=5e-3, r=0.25)
    try:
        decoder = DigitDecoder()
    except Exception:
        decoder = None   # no model yet; teach some words first
    msg = "SPACE: speak aloud = teach | mouth silently = predict"
    last_bars = None

    try:
        while True:
            source.read()
            ok = getattr(source, "last_face_ok", True)
            lines = [("SPACE = go     ESC = quit", (255, 255, 255), 0.7),
                     (f"face: {'YES' if ok else 'NO'}",
                      (0, 255, 0) if ok else (0, 0, 255), 0.7),
                     (msg, (0, 220, 220), 0.7)]
            _draw(source, lines, last_bars)
            k = cv2.waitKey(1) & 0xFF
            if k == 27:
                break
            if k != 32:
                continue

            raw, t, audio = record_window(source)
            if len(raw) < 3:
                msg = "no lip signal - try again"
                last_bars = None
                continue
            rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
            _save_wav(DEBUG_WAV, audio)   # for diagnosing capture quality

            if rms > VOICED_RMS:
                # --- TEACH: transcribe, let the user confirm/fix, then retrain ---
                text, words = asr.transcribe_array(audio, SR)
                heard = words[0]["word"] if words else (text.split()[0] if text else "")
                action, label = review_asr(source, heard)
                if action == "discard":
                    msg = "discarded - nothing saved"
                    last_bars = None
                    continue
                if action == "redo":
                    msg = "redo: press SPACE and say it again"
                    last_bars = None
                    continue
                _draw(source, [(f"teaching '{label}'...", (0, 255, 255), 1.0)])
                cv2.waitKey(1)
                record_session._save(source, label, raw, t)
                train_and_save()
                decoder = DigitDecoder()
                msg = f"learned '{label}' -> retrained"
                last_bars = None
            else:
                # --- PREDICT: silent mouthing ---
                if decoder is None:
                    msg = "no model yet - teach a few words out loud first"
                    continue
                label, conf, ranked = decoder.predict(ks.smooth(raw))
                msg = f"GUESS (silent): {label}  ({conf:.0%})"
                last_bars = ranked[:3]
    finally:
        source.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
