# Trains a LoRA on the support_email domain, once the GPU is free.
#
# The GPU is single-tenant at 8GB, so this waits for any running score_local /
# train_local process to exit before starting. It also waits for the email
# cycle to have produced a dataset.
#
#   .\scripts\train_email_lora.ps1

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$py = ".\.venv\Scripts\python.exe"

$env:DB_PATH = "autoextract.email.db"
$env:DOMAIN = "support_email"
$env:COMPACT_PROMPT = "1"
$env:JSON_MODE = "1"
$env:ENRICH = "1"

function Log($m) { Write-Host "[emailLoRA $(Get-Date -Format HH:mm:ss)] $m" -ForegroundColor Magenta }

function Wait-ForGpu {
    Log "waiting for the GPU to be free..."
    while ($true) {
        $n = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
              Where-Object { $_.CommandLine -match "score_local|train_local" } |
              Measure-Object).Count
        if ($n -eq 0) { Log "GPU free"; return }
        Start-Sleep -Seconds 30
    }
}

Log "waiting for the email cycle to produce a dataset..."
while ($true) {
    if (Test-Path "logs\email_cycle.log") {
        $c = Get-Content "logs\email_cycle.log" -Raw -ErrorAction SilentlyContinue
        if ($c -match "EMAIL CYCLE COMPLETE") { Log "email cycle done"; break }
    }
    Start-Sleep -Seconds 30
}

Wait-ForGpu
Log "scoring the email base model"
& $py scripts\score_local.py --label email-base *>&1 | Out-File logs\email_score_base.log

Wait-ForGpu
Log "training the email LoRA"
& $py scripts\train_local.py --epochs 3 --lora-r 16 *>&1 | Out-File logs\email_train.log

$line = Get-Content logs\email_train.log -ErrorAction SilentlyContinue |
        Select-String -Pattern "adapter saved -> " | Select-Object -Last 1
if (-not $line) { Log "training produced no adapter - stopping"; exit 1 }
$adapter = ($line -replace '.*adapter saved -> ', '').Trim()
Log "adapter: $adapter"

Wait-ForGpu
Log "scoring the email LoRA"
& $py scripts\score_local.py --label email-lora --adapter $adapter *>&1 |
    Out-File logs\email_score_lora.log

Log "comparing"
& $py scripts\selfheal_test.py compare --before email-base --after email-lora *>&1 |
    Tee-Object -FilePath logs\email_compare.log

Log "registering with the gate"
& $py scripts\register_local.py --base email-base --lora email-lora --adapter $adapter *>&1 |
    Tee-Object -FilePath logs\email_register.log

Log "EMAIL LORA COMPLETE"
