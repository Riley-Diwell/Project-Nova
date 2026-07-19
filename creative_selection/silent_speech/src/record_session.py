"""Labeled data recorder for the silent-speech pipeline.

Gets *consistent* data in: for each prompt (word/phrase) it records a stream of
feature vectors from a SignalSource, Kalman-smooths it, and saves a labeled
sample to data/. Written against the SignalSource interface, so the very same
recorder will capture EMG data later with no changes here.

Two pacing modes:
  * manual (default, GUI): live preview; press SPACE to record each prompt when
    you're ready, S to skip, ESC to quit. Lets you lock in before each word.
  * auto (--auto or headless): records each prompt on a fixed timer.

Samples are written to the single HDF5 store (see dataset_store); only raw
float32 features are kept, smoothing is applied on load.

Usage:
    python record_session.py --labels zero,one,two --reps 5          # manual
    python record_session.py --labels hello,world --auto --no-gui    # headless
"""
from __future__ import annotations

import argparse
import os
import time

import numpy as np

import dataset_store

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DEFAULT_LABELS = ["zero", "one", "two", "three", "four",
                  "five", "six", "seven", "eight", "nine"]


def _preview(source, lines):
    """Show the source's live preview frame (if any) with overlaid text lines."""
    import cv2
    frame = getattr(source, "last_frame", None)
    frame = np.zeros((480, 640, 3), np.uint8) if frame is None else frame.copy()
    y = 45
    for text, col in lines:
        cv2.putText(frame, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, col, 2)
        y += 42
    cv2.imshow("recorder", frame)


def _wait_ready(source, label, rep, reps, voicing="voiced"):
    """Live-preview loop until the user acts. Returns 'record'|'skip'|'quit'."""
    import cv2
    cue = "SAY it aloud" if voicing == "voiced" else "MOUTH it (no sound)"
    while True:
        source.read()  # keep camera live and refresh preview
        ok = getattr(source, "last_face_ok", True)
        _preview(source, [
            (f"READY: '{label}'  ({cue})  (rep {rep}/{reps})", (0, 255, 0)),
            ("SPACE = record    S = skip    ESC = quit", (255, 255, 255)),
            (f"face detected: {'YES' if ok else 'NO'}",
             (0, 255, 0) if ok else (0, 0, 255)),
        ])
        k = cv2.waitKey(1) & 0xFF
        if k == 32:
            return "record"
        if k in (ord("s"), ord("S")):
            return "skip"
        if k == 27:
            return "quit"


def _review(source, label, n_frames, det):
    """After a take, let the user keep/redo/quit. Returns the choice string."""
    import cv2
    while True:
        source.read()  # keep camera live during review
        _preview(source, [
            (f"REVIEW '{label}':  {n_frames} frames  face {det:.0%}",
             (0, 255, 255)),
            ("SPACE = keep    R = redo    ESC = quit", (255, 255, 255)),
        ])
        k = cv2.waitKey(1) & 0xFF
        if k == 32:
            return "keep"
        if k in (ord("r"), ord("R")):
            return "redo"
        if k == 27:
            return "quit"


def _capture_manual(source, label, rep, reps, duration, voicing="voiced"):
    """Ready -> record -> review loop. Returns (status, raw, t, det).

    status is 'save' | 'skip' | 'quit'. Lets the user redo a bad take before it
    is ever saved.
    """
    while True:
        action = _wait_ready(source, label, rep, reps, voicing)
        if action == "quit":
            return "quit", None, None, 0.0
        if action == "skip":
            return "skip", None, None, 0.0
        raw, t, det = _record(source, label, duration, gui=True, manual=True,
                              voicing=voicing)
        if len(raw) < 2:  # nothing captured; loop back to ready
            continue
        decision = _review(source, label, len(raw), det)
        if decision == "keep":
            return "save", raw, t, det
        if decision == "quit":
            return "quit", None, None, 0.0
        # decision == "redo": loop back to ready and try again


def _record(source, label, duration, gui, manual, voicing="voiced"):
    """Record one utterance. Returns (raw TxD, times T, det_rate)."""
    import cv2
    verb = "SPEAK" if voicing == "voiced" else "MOUTH"
    frames, times = [], []
    t0 = time.time()
    detected = seen = 0
    while time.time() - t0 < duration:
        feat = source.read()
        seen += 1
        if feat is not None:
            frames.append(feat)
            times.append(time.time() - t0)
            detected += 1
        if gui:
            elapsed = time.time() - t0
            if manual:
                _preview(source, [
                    (f"REC '{label}'", (0, 0, 255)),
                    (f"{elapsed:0.1f} / {duration:0.1f}s", (0, 0, 255)),
                ])
            else:
                _preview(source, [(f"{verb}: {label}", (0, 255, 0)),
                                  (f"REC {elapsed:0.1f}s", (0, 0, 255))])
            cv2.waitKey(1)
    return np.array(frames), np.array(times), detected / max(seen, 1)


def _save(source, label, raw, t, voicing="voiced", out_dir=None):
    fps = len(raw) / (t[-1] - t[0]) if len(t) > 1 and t[-1] > t[0] else 0.0
    idx = dataset_store.append(raw, label, modality=source.modality,
                               feature_names=source.feature_names,
                               voicing=voicing)
    return idx, fps


def record_dataset(source, labels, reps, duration, gui, manual=True,
                   voicing="voiced", out_dir=DATA_DIR, countdown=1.0):
    os.makedirs(out_dir, exist_ok=True)
    manual = manual and gui  # manual pacing needs the GUI for keypresses
    source.open()
    saved = []
    try:
        for rep in range(1, reps + 1):
            for label in labels:
                if manual:
                    status, raw, t, det = _capture_manual(
                        source, label, rep, reps, duration, voicing)
                    if status == "quit":
                        print("Quit requested.")
                        return _finish(saved, out_dir)
                    if status == "skip":
                        print(f"[rep {rep}/{reps}] skipped '{label}'")
                        continue
                else:
                    print(f"[rep {rep}/{reps}] get ready: '{label}' ...",
                          flush=True)
                    time.sleep(countdown)
                    raw, t, det = _record(source, label, duration, gui, manual)
                if len(raw) < 2:
                    print(f"  SKIPPED '{label}': no signal (face {det:.0%})")
                    continue
                idx, fps = _save(source, label, raw, t, voicing, out_dir)
                saved.append(idx)
                print(f"  [rep {rep}/{reps}] '{label}': {len(raw)} frames "
                      f"@ {fps:.1f}fps (face {det:.0%}) -> sample #{idx}",
                      flush=True)
    finally:
        source.close()
        try:
            import cv2
            cv2.destroyAllWindows()
        except Exception:
            pass
    return _finish(saved, out_dir)


def _finish(saved, out_dir):
    print(f"\nDone. {len(saved)} utterances saved to {out_dir}")
    return saved


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=",".join(DEFAULT_LABELS))
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--duration", type=float, default=1.5)
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--backend", default="dshow")
    ap.add_argument("--auto", dest="manual", action="store_false",
                    help="fixed-timer pacing instead of SPACE-to-record")
    ap.add_argument("--no-gui", dest="gui", action="store_false")
    ap.add_argument("--silent", dest="voicing", action="store_const",
                    const="silent", default="voiced",
                    help="mouth words silently (tags samples voicing=silent)")
    args = ap.parse_args()

    from signal_source import WebcamLipSource
    src = WebcamLipSource(index=args.index, backend=args.backend)
    labels = [s.strip() for s in args.labels.split(",") if s.strip()]
    record_dataset(src, labels, args.reps, args.duration, args.gui, args.manual,
                   voicing=args.voicing)
