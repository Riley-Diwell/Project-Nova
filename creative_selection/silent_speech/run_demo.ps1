# One-click launcher for the CONTINUOUS silent-speech app.
#   VOICED (default): speak naturally -> live caption + continuous auto-labeled,
#     phoneme-tagged training.  SPACE -> SILENT: mouth words, it predicts.
#   ESC = quit.
# Right-click > Run with PowerShell, or run:  .\run_demo.ps1
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
& "$root\.venv\Scripts\python.exe" "$root\src\continuous.py"
