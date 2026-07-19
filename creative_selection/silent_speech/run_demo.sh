#!/usr/bin/env bash
# One-click launcher for the CONTINUOUS silent-speech app.
#   VOICED (default): speak naturally -> live caption + continuous auto-labeled,
#     phoneme-tagged training.  SPACE -> SILENT: mouth words, it predicts.
#   ESC = quit.
# Run with:  ./run_demo.sh   (or double-click after chmod +x)

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$root/.venv/bin/python" "$root/src/continuous.py"