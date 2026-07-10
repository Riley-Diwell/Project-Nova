"""Shared camera helper.

On this machine the live camera is index 0 (the index-1 device is a covered /
shuttered camera that returns near-black frames). DirectShow (dshow) is the
reliable backend here and is the default; MSMF hangs on open on this machine.
"""
import time
import cv2

BACKENDS = {"msmf": cv2.CAP_MSMF, "dshow": cv2.CAP_DSHOW, "any": cv2.CAP_ANY}


def open_camera(index: int = 0, backend: str = "dshow",
                width: int = 640, height: int = 480, warmup: float = 1.0):
    """Open a camera, set resolution, and warm up so auto-exposure settles.

    Returns an opened cv2.VideoCapture, or raises RuntimeError if no frame.
    """
    cap = cv2.VideoCapture(index, BACKENDS.get(backend, cv2.CAP_ANY))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera index {index} on backend {backend!r}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    t_end = time.time() + warmup
    frame = None
    while time.time() < t_end:
        ok, f = cap.read()
        if ok and f is not None:
            frame = f
    if frame is None:
        cap.release()
        raise RuntimeError(f"Camera index {index} opened but delivered no frames")
    return cap
