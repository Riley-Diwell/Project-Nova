# One-shot: record a FOCUSED starter vocabulary (VOICED + SILENT), then retrain.
#
# Fixes two things at once:
#  * "predictions are wrong": the auto-labeled store was 300+ phrases each seen
#    once, which a CTC model can't learn from. This records a small closed
#    vocabulary with many reps each -- the repetition is what lets it generalize.
#  * the voiced/silent DOMAIN GAP: silent mode predicts on mouthed (silent) lip
#    motion but the model only ever saw voiced motion. So we record each word BOTH
#    ways -- say it aloud, then mouth it silently -- and train on the mix (a la
#    Gaddy's parallel voiced/silent EMG). Samples are tagged voicing=voiced|silent
#    so `phoneme_model.py --eval` reports a separate silent-acc number.
#
# Default words are common AND spread across lip-shapes (visemes) so they look
# distinct on the mouth: bilabial P/B/M (please, help, more), rounded OO/OW
# (no, go, food, more), open AA (stop, water), spread EH (yes, hello), F (food).
# All are plain CMUdict words, so the trainer looks up their phonemes automatically.
#
# Usage:
#   .\collect_and_train.ps1                        # 10 words x15 voiced + x15 silent
#   .\collect_and_train.ps1 -Reps 20               # more reps each pass
#   .\collect_and_train.ps1 -SilentReps 0          # voiced only (skip silent pass)
#   .\collect_and_train.ps1 -Labels "cat,dog,red"  # custom vocabulary
#
# In the recorder: SPACE = record a take, R = redo, S = skip, ESC = end that pass.
param(
    [string]$Labels = "yes,no,go,stop,hello,please,help,water,food,more",
    [int]$Reps = 15,
    [int]$SilentReps = -1,      # -1 => same as -Reps; 0 => skip the silent pass
    [double]$Duration = 1.5,
    [int]$Epochs = 300
)
if ($SilentReps -lt 0) { $SilentReps = $Reps }
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = "$root\.venv\Scripts\python.exe"
$rec = "$root\src\record_session.py"

Write-Host "=== 1/3  VOICED pass -- SAY each word aloud  ($Reps reps) ===" -ForegroundColor Cyan
Write-Host "SPACE=record  R=redo  S=skip  ESC=end pass`n"
& $py $rec --labels $Labels --reps $Reps --duration $Duration

if ($SilentReps -gt 0) {
    Write-Host "`n=== 2/3  SILENT pass -- MOUTH each word, NO sound  ($SilentReps reps) ===" -ForegroundColor Cyan
    Write-Host "Same words, mouthed silently. SPACE=record  R=redo  S=skip  ESC=end pass`n"
    & $py $rec --labels $Labels --reps $SilentReps --duration $Duration --silent
} else {
    Write-Host "`n=== 2/3  SILENT pass skipped (-SilentReps 0) ===" -ForegroundColor DarkGray
}

Write-Host "`n=== 3/3  Training phoneme CTC model ($Epochs epochs) ===" -ForegroundColor Cyan
& $py "$root\src\phoneme_model.py" --train --epochs $Epochs
if ($LASTEXITCODE -ne 0) { Write-Host "Training failed (exit $LASTEXITCODE)." -ForegroundColor Red; exit 1 }

Write-Host "`nDone. 'silent-acc' in the training log is the number that matters." -ForegroundColor Green
Write-Host "Launch .\run_demo.ps1 and press SPACE for SILENT mode to test." -ForegroundColor Green
