"""Live silent-speech demo with human-in-the-loop reinforcement.

Pipeline:  webcam -> lip landmarks -> Kalman -> decoder -> digit + confidence

After each guess you tell it whether it was right or wrong. Every confirmed /
corrected utterance is saved as new labeled data and the model RETRAINS on the
spot, so it adapts to live conditions and to you (active learning).

Controls:
    SPACE            record ~1.5s while you silently mouth a digit, then guess
    Y                the guess was correct   -> save + retrain
    N                the guess was wrong     -> then press the true digit 0-9
    0-9              (after N) the correct digit -> save + retrain
    C                cancel a correction
    ESC              quit
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from signal_source import WebcamLipSource
from kalman import KalmanSmoother
from decoder import DigitDecoder, train_and_save
import record_session

DURATION = 1.5
KALMAN_Q, KALMAN_R = 5e-3, 0.25
DIGIT_WORDS = ["zero", "one", "two", "three", "four",
               "five", "six", "seven", "eight", "nine"]


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
            w = int(prob * 260)
            cv2.rectangle(frame, (150, by - 15), (150 + w, by), (0, 200, 0), -1)
            cv2.putText(frame, label, (18, by), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (255, 255, 255), 1)
            cv2.putText(frame, f"{prob:.0%}", (160 + w, by),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
            by += 26
    cv2.imshow("Silent Speech - Live Demo", frame)


def record_once(source):
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
    return np.array(raw), np.array(times)


def main():
    source = WebcamLipSource()
    decoder = DigitDecoder()
    source.open()
    ks = KalmanSmoother(source.n_features, dt=1.0, q=KALMAN_Q, r=KALMAN_R)

    mode = "idle"          # idle | feedback | correct
    last = None            # (label, conf, ranked)
    last_raw = last_t = None
    correct_n = total_n = 0
    msg = ""

    def learn(label):
        nonlocal decoder, msg
        record_session._save(source, label, last_raw, last_t)
        _draw(source, [("learning...", (0, 255, 255), 1.0)])
        cv2.waitKey(1)
        train_and_save()
        decoder = DigitDecoder()   # reload freshly trained model
        msg = f"learned '{label}'  (dataset grew)"

    try:
        while True:
            source.read()
            ok = getattr(source, "last_face_ok", True)

            if mode == "idle":
                lines = [("SPACE = speak a digit    ESC = quit",
                          (255, 255, 255), 0.7),
                         (f"face: {'YES' if ok else 'NO'}",
                          (0, 255, 0) if ok else (0, 0, 255), 0.7)]
                if total_n:
                    lines.append((f"session score: {correct_n}/{total_n}",
                                  (0, 255, 255), 0.7))
                if msg:
                    lines.append((msg, (0, 220, 0), 0.6))
                bars = last[2][:3] if last else None
                if last:
                    lines.insert(0, (f"GUESS: {last[0]}  ({last[1]:.0%})",
                                     (0, 255, 255), 1.2))
                _draw(source, lines, bars)

            elif mode == "feedback":
                label, conf, ranked = last
                _draw(source, [
                    (f"GUESS: {label}  ({conf:.0%})", (0, 255, 255), 1.2),
                    ("Correct?   Y = yes    N = no", (255, 255, 255), 0.8),
                    ("ESC = quit", (180, 180, 180), 0.6),
                ], ranked[:3])

            elif mode == "correct":
                _draw(source, [
                    (f"Guess was '{last[0]}'. What did you say?",
                     (0, 0, 255), 0.8),
                    ("Press the digit key 0-9    C = cancel",
                     (255, 255, 255), 0.8),
                ])

            k = cv2.waitKey(1) & 0xFF
            if k == 27:
                break

            if mode == "idle" and k == 32:
                raw, t = record_once(source)
                if len(raw) < 3:
                    msg = "no signal - try again"
                    continue
                last_raw, last_t = raw, t
                last = decoder.predict(ks.smooth(raw))
                msg = ""
                mode = "feedback"

            elif mode == "feedback":
                if k in (ord("y"), ord("Y")):
                    total_n += 1
                    correct_n += 1
                    learn(last[0])
                    mode = "idle"
                elif k in (ord("n"), ord("N")):
                    total_n += 1
                    mode = "correct"

            elif mode == "correct":
                if k in (ord("c"), ord("C")):
                    mode = "idle"
                elif ord("0") <= k <= ord("9"):
                    learn(DIGIT_WORDS[k - ord("0")])
                    mode = "idle"
    finally:
        source.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
