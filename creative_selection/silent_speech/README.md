# Silent Speech Interpreter

Camera-based silent speech interface POC. Reads lip movement from a webcam, tracks
lip landmarks, and decodes mouthed words via a phoneme classifier. Next step is to
reconstruct uncertain words using an LLM. All processing runs locally.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The face model is pinned locally at `models/face_landmarker.task` (MediaPipe,
downloaded once from Google's public CDN — no auth). Runtime is fully offline.

## Run

```powershell
# Live preview window with lip overlay (ESC or q to quit)
.\.venv\Scripts\python.exe src\lip_landmarks.py
# python src/lip_landmarks.py --backend any for mac

# Headless: process N frames, no window, save annotated snapshot for verification
.\.venv\Scripts\python.exe src\lip_landmarks.py --headless 30
# python src/lip_landmarks.py --headless 30 --backend any

# Enumerate cameras and report which are live vs covered
.\.venv\Scripts\python.exe src\list_cameras.py
# python src/list_cameras.py for mac
```