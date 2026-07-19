# Ongoing CTC trainer. Run this in a SECOND terminal alongside run_demo.ps1:
# it continuously retrains the phoneme model on the growing dataset store and
# writes checkpoints that the app hot-reloads. Ctrl+C to stop.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
& "$root\.venv\Scripts\python.exe" -u "$root\src\train_daemon.py"
