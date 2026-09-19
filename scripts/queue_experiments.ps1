# Runs the remaining GPU experiments back to back, waiting for the current
# scoring pass to finish first. The GPU is single-tenant at 8GB -- starting a
# second model load alongside a running one OOMs both -- so this serialises
# them rather than trying to overlap.
#
# Order:
#   1. register the main local run (CPU) so the dashboard reflects it
#   2. control LoRA  -> train, score        (the negative control, #2)
#   3. undertrained  -> train, score, gate  (the deliberate rejection, #3)
#
#   .\scripts\queue_experiments.ps1

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$py = ".\.venv\Scripts\python.exe"
$env:COMPACT_PROMPT = "1"     # PowerShell assignment: no trailing-space trap

function Log($msg) { Write-Host "[queue $(Get-Date -Format HH:mm:ss)] $msg" -ForegroundColor Cyan }

function Wait-ForLog($path, $needle, $label) {
    Log "waiting for $label ..."
    while ($true) {
        if (Test-Path $path) {
            $c = Get-Content $path -Raw -ErrorAction SilentlyContinue
            if ($c -match [regex]::Escape($needle)) { Log "$label done"; return $true }
            if ($c -match "Traceback") { Log "$label FAILED"; return $false }
        }
        Start-Sleep -Seconds 20
    }
}

function Adapter-FromLog($path) {
    $line = (Get-Content $path -ErrorAction SilentlyContinue |
             Select-String -Pattern "adapter saved -> " | Select-Object -Last 1)
    if (-not $line) { return $null }
    return ($line -replace '.*adapter saved -> ', '').Trim()
}

# --- wait for the in-flight base+lora scoring -------------------------------
if (-not (Wait-ForLog "logs\score_lora.log" "wrote" "main lora scoring")) { exit 1 }

# --- 1. register the main run ----------------------------------------------
$mainAdapter = Adapter-FromLog "logs\train.log"
Log "registering main run ($mainAdapter)"
& $py scripts\register_local.py --base local-base --lora local-lora --adapter $mainAdapter 2>&1 |
    Tee-Object -FilePath logs\register_main.log

# --- 2. negative control ----------------------------------------------------
$controlDs = (Get-ChildItem data\datasets\control-*.jsonl | Sort-Object Name | Select-Object -Last 1).FullName
Log "training CONTROL on $controlDs"
& $py scripts\train_local.py --dataset $controlDs --epochs 3 --lora-r 16 *>&1 |
    Out-File logs\train_control.log
$controlAdapter = Adapter-FromLog "logs\train_control.log"
if ($controlAdapter) {
    Log "scoring CONTROL ($controlAdapter)"
    & $py scripts\score_local.py --label local-control --adapter $controlAdapter *>&1 |
        Out-File logs\score_control.log
} else { Log "CONTROL training produced no adapter - skipping score" }

# --- 3. deliberately undertrained candidate --------------------------------
$underDs = (Get-ChildItem data\datasets\undertrained-*.jsonl | Sort-Object Name | Select-Object -Last 1).FullName
Log "training UNDERTRAINED on $underDs (20 examples, 1 epoch)"
& $py scripts\train_local.py --dataset $underDs --epochs 1 --lora-r 8 *>&1 |
    Out-File logs\train_under.log
$underAdapter = Adapter-FromLog "logs\train_under.log"
if ($underAdapter) {
    Log "scoring UNDERTRAINED ($underAdapter)"
    & $py scripts\score_local.py --label local-undertrained --adapter $underAdapter *>&1 |
        Out-File logs\score_under.log
    Log "registering UNDERTRAINED - the gate should REJECT this one"
    & $py scripts\register_local.py --base local-base --lora local-undertrained `
        --adapter $underAdapter 2>&1 | Tee-Object -FilePath logs\register_under.log
} else { Log "UNDERTRAINED training produced no adapter" }

Log "ALL EXPERIMENTS COMPLETE"
