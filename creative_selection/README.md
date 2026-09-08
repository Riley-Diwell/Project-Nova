# creative_selection

Pre-v1 prototypes and experiments (2026-07-07 to 2026-07-25), kept for historical
reference. None of this is active — current work lives in [`nova_v1/`](../nova_v1)
and [`nova_v2/`](../nova_v2).

- **`esp32/`, `intent_surface/`, `memory_and_retrieval/`, `silent_speech/`** — early
  individual prototyping (Georgia, Ella): ESP32 BLE firmware demo, a physiological
  "intent surface" (heart rate/gaze/posture context), silent-speech (lip-reading)
  input research, and a vector-memory sketch. Conceptually closest to the on-device
  hardware peripheral v2 is aiming for, but predates the `nova_v2/` folder and isn't
  wired into it.
- **`notification_batcher/`** (Ella) — notification urgency batching + an LED-ring
  hardware bridge. Referenced by `nova_main.py` but never fully wired up.
- **`nova_main.py`** — an early integration sketch tying the above together. Never
  ran end-to-end: it imports from sibling folders (`../nova_code`, `../nova_georgia`)
  that don't exist in this repo.
- **`environment.yml`** — the conda environment used for this round of prototyping.

Superseded/duplicate code from this era (an old `mcp_tool_interface_controller_gain/`
explicitly marked "do not use", and a stale duplicate root-level `silent_speech/`)
was deleted rather than kept here — see git history if you need it back.
