"""Modality-agnostic signal sources for the silent-speech pipeline.

The whole downstream stack (Kalman smoother, decoder, LLM) consumes a stream of
fixed-length feature vectors and does NOT care where they came from. Today the
source is webcam lip landmarks; later it will be EMG channels. Both implement
the same SignalSource interface, so nothing downstream changes when we swap.

    source = WebcamLipSource()          # or EMGSource() in the future
    source.open()
    while True:
        feat = source.read()            # np.ndarray shape (n_features,) or None
        ...
    source.close()
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

import numpy as np


class SignalSource(ABC):
    """A stream of per-frame feature vectors from some silent-speech sensor."""

    #: number of scalar features per frame
    n_features: int
    #: human-readable name per feature (len == n_features)
    feature_names: list[str]
    #: modality tag stored with recorded data ("webcam_lips", "emg", ...)
    modality: str

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def read(self) -> np.ndarray | None:
        """Return one feature vector (n_features,), or None if unavailable."""

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()


# Canonical MediaPipe FaceMesh lip landmark indices (outer + inner contour).
OUTER_LIP = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291,
             409, 270, 269, 267, 0, 37, 39, 40, 185]
INNER_LIP = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308,
             415, 310, 311, 312, 13, 82, 81, 80, 191]
LIP_IDX = OUTER_LIP + INNER_LIP
EYE_L, EYE_R = 33, 263      # outer eye corners: a stable facial reference


def face_normalized_lips(landmarks, w: int, h: int) -> np.ndarray:
    """Lip landmarks normalized against a STABLE facial reference (the eyes).

    Origin = midpoint between the eye corners; scale = inter-ocular distance;
    coordinates rotated by the eye-line angle. Unlike per-frame lip-box
    normalization, this PRESERVES mouth openness (measured in units of eye
    spacing) and is invariant to face position, distance, and head roll.
    Returns (40, 2).
    """
    lx, ly = landmarks[EYE_L].x * w, landmarks[EYE_L].y * h
    rx, ry = landmarks[EYE_R].x * w, landmarks[EYE_R].y * h
    ox, oy = (lx + rx) / 2, (ly + ry) / 2
    dx, dy = rx - lx, ry - ly
    scale = float(np.hypot(dx, dy)) or 1.0
    ca, sa = dx / scale, dy / scale          # cos/sin of the eye-line angle
    out = np.empty((len(LIP_IDX), 2))
    for k, i in enumerate(LIP_IDX):
        px = landmarks[i].x * w - ox
        py = landmarks[i].y * h - oy
        out[k, 0] = (px * ca + py * sa) / scale     # rotate by -angle, rescale
        out[k, 1] = (-px * sa + py * ca) / scale
    return out


class WebcamLipSource(SignalSource):
    """Webcam lip landmarks -> 80-dim feature vector (40 points x, y).

    Coordinates are normalized against a stable facial reference (the eyes) via
    face_normalized_lips(), so the feature is invariant to face position,
    distance, and head roll while PRESERVING mouth openness -- the key phoneme
    cue that the older per-frame lip-box normalization discarded.
    """

    modality = "webcam_lips"

    def __init__(self, index: int = 0, backend: str = "any"):
        self.index = index
        self.backend = backend
        self.n_features = len(LIP_IDX) * 2
        self.feature_names = [f"{ax}{i}" for i in LIP_IDX for ax in ("x", "y")]
        self._cap = None
        self._landmarker = None
        self._t0 = None
        self.last_face_ok = False
        #: last annotated BGR frame, for optional live preview (webcam only)
        self.last_frame = None

    def open(self) -> None:
        import time
        import cv2  # noqa: F401  (imported for side effect / availability)
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        from camera import open_camera

        model_path = os.path.join(os.path.dirname(__file__), "..",
                                  "models", "face_landmarker.task")
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Missing model: {model_path}")
        self._mp = mp
        self._cap = open_camera(self.index, self.backend)
        opts = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.VIDEO, num_faces=1)
        self._landmarker = vision.FaceLandmarker.create_from_options(opts)
        self._t0 = time.time()

    def read(self) -> np.ndarray | None:
        import time
        import cv2
        ok, frame = self._cap.read()
        if not ok:
            self.last_face_ok = False
            self.last_frame = None
            return None
        frame = cv2.flip(frame, 1)  # mirror for natural preview
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        ts_ms = int((time.time() - self._t0) * 1000)
        result = self._landmarker.detect_for_video(image, ts_ms)
        if not result.face_landmarks:
            self.last_face_ok = False
            self.last_frame = frame
            return None
        self.last_face_ok = True
        lm = result.face_landmarks[0]
        # Draw lip landmarks onto the preview frame.
        for i in LIP_IDX:
            cv2.circle(frame, (int(lm[i].x * w), int(lm[i].y * h)), 1,
                       (0, 255, 0), -1)
        # Eye-corner reference points used for scale/rotation invariance.
        for i in (EYE_L, EYE_R):
            cv2.circle(frame, (int(lm[i].x * w), int(lm[i].y * h)), 3,
                       (0, 255, 0), -1)
        # Pupils (iris centers), if the model provides iris landmarks (478).
        for i in (468, 473):
            if i < len(lm):
                cv2.circle(frame, (int(lm[i].x * w), int(lm[i].y * h)), 3,
                           (0, 255, 0), -1)
        self.last_frame = frame
        return face_normalized_lips(lm, w, h).flatten()

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
        if self._landmarker is not None:
            self._landmarker.close()
