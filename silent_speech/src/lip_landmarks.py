"""Live lip-landmark tracking — the front-end of the silent-speech pipeline.

Runs MediaPipe FaceLandmarker (Tasks API) on the webcam, isolates the 40
canonical lip landmarks (outer + inner contour), overlays them, and computes a
per-frame normalized lip feature vector. Those raw landmarks are exactly what
the Kalman smoother will consume downstream before the phoneme classifier.

Fully offline: the model is loaded from models/face_landmarker.task on disk;
no network calls, no accounts, no data leaves the machine.

Usage:
    python lip_landmarks.py                 # live preview window (ESC/q to quit)
    python lip_landmarks.py --headless 30   # no GUI: process 30 frames, save an
                                            # annotated snapshot + report status
    python lip_landmarks.py --index 0 --backend msmf
"""
import argparse
import os
import time

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from camera import open_camera

BASE_DIR = os.path.dirname(__file__)
SNAP_DIR = os.path.join(BASE_DIR, "..", "snapshots")
MODEL_PATH = os.path.join(BASE_DIR, "..", "models", "face_landmarker.task")

# Canonical MediaPipe FaceMesh lip landmark indices (outer + inner contour).
OUTER_LIP = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291,
             409, 270, 269, 267, 0, 37, 39, 40, 185]
INNER_LIP = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308,
             415, 310, 311, 312, 13, 82, 81, 80, 191]
LIP_IDX = OUTER_LIP + INNER_LIP


def lip_feature(landmarks, w: int, h: int) -> np.ndarray:
    """Return (40, 2) lip landmark coords, normalized to the lip bounding box.

    Normalizing by the lip box makes the feature roughly invariant to how close
    the face is to the camera and where it sits in the frame.
    """
    pts = np.array([[landmarks[i].x * w, landmarks[i].y * h] for i in LIP_IDX])
    mins = pts.min(axis=0)
    span = np.ptp(pts, axis=0)
    span[span == 0] = 1.0
    return (pts - mins) / span


def draw_lips(frame, landmarks, w: int, h: int) -> None:
    for i in LIP_IDX:
        x, y = int(landmarks[i].x * w), int(landmarks[i].y * h)
        cv2.circle(frame, (x, y), 1, (0, 255, 0), -1)


def make_landmarker() -> vision.FaceLandmarker:
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(
            f"Model not found at {MODEL_PATH}. Download face_landmarker.task first.")
    options = vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=vision.RunningMode.VIDEO,
        num_faces=1)
    return vision.FaceLandmarker.create_from_options(options)


def run(index: int, backend: str, headless: int) -> None:
    os.makedirs(SNAP_DIR, exist_ok=True)
    cap = open_camera(index, backend)
    landmarker = make_landmarker()

    frames = 0
    detected = 0
    t0 = time.time()

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame = cv2.flip(frame, 1)  # mirror for natural preview
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        ts_ms = int((time.time() - t0) * 1000)
        result = landmarker.detect_for_video(mp_image, ts_ms)

        has_face = bool(result.face_landmarks)
        if has_face:
            detected += 1
            lm = result.face_landmarks[0]
            draw_lips(frame, lm, w, h)
            feat = lip_feature(lm, w, h)  # noqa: F841 -> downstream input

        frames += 1
        fps = frames / (time.time() - t0)

        if headless:
            if frames >= headless:
                path = os.path.join(SNAP_DIR, "lip_overlay.jpg")
                cv2.imwrite(path, frame)
                print(f"Processed {frames} frames, face detected in {detected}.")
                print(f"Avg FPS: {fps:.1f}")
                print(f"Annotated snapshot -> {path}")
                break
        else:
            cv2.putText(frame, f"FPS: {fps:.1f}  face:{'Y' if has_face else 'N'}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.imshow("Silent Speech - Lip Tracking (ESC/q to quit)", frame)
            if (cv2.waitKey(1) & 0xFF) in (27, ord("q")):
                break

    cap.release()
    landmarker.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--backend", default="dshow", choices=["msmf", "dshow", "any"])
    ap.add_argument("--headless", type=int, default=0,
                    help="Process N frames with no GUI window and save a snapshot.")
    args = ap.parse_args()
    run(args.index, args.backend, args.headless)
