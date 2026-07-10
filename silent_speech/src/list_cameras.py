"""Probe available webcams and confirm they actually deliver *live* frames.

Tries both Windows backends (MSMF default + DirectShow), warms each camera up
for ~2s so auto-exposure can ramp, and reports mean brightness so a covered /
shuttered camera (near-black frame) is distinguishable from a live one.
Saves a snapshot per working camera to snapshots/ for visual verification.
"""
import os
import time
import cv2

SNAP_DIR = os.path.join(os.path.dirname(__file__), "..", "snapshots")

BACKENDS = [("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW)]


def try_camera(idx: int, backend_name: str, backend_id: int) -> dict | None:
    cap = cv2.VideoCapture(idx, backend_id)
    if not cap.isOpened():
        cap.release()
        return None
    # Warm up ~2s: let auto-exposure/auto-gain settle before judging brightness.
    frame = None
    t_end = time.time() + 2.0
    while time.time() < t_end:
        ok, f = cap.read()
        if ok and f is not None:
            frame = f
        time.sleep(0.03)
    if frame is None:
        cap.release()
        return None
    h, w = frame.shape[:2]
    fps = cap.get(cv2.CAP_PROP_FPS)
    brightness = float(frame.mean())  # 0-255
    path = os.path.join(SNAP_DIR, f"camera_{idx}_{backend_name}.jpg")
    cv2.imwrite(path, frame)
    cap.release()
    return {"index": idx, "backend": backend_name, "width": w, "height": h,
            "fps": fps, "brightness": brightness, "snapshot": path}


def probe(max_index: int = 4) -> list[dict]:
    os.makedirs(SNAP_DIR, exist_ok=True)
    working = []
    for name, bid in BACKENDS:
        for idx in range(max_index):
            info = try_camera(idx, name, bid)
            if info is None:
                continue
            state = "LIVE" if info["brightness"] > 12 else "DARK (covered?)"
            print(f"[OK] {name} index {idx}: {info['width']}x{info['height']} "
                  f"brightness={info['brightness']:.1f} -> {state}")
            working.append(info)
    if not working:
        print("No working cameras found on any backend.")
    return working


if __name__ == "__main__":
    probe()
