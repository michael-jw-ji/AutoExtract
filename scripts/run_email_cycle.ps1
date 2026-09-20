# Runs the full loop on the support_email domain once generation finishes.
#
# Waits for scripts/gen_emails.py to land, then: freeze -> baseline -> serve
# -> repair -> clusters -> build a training dataset. Training itself is left
# to a separate step because the GPU is single-tenant.
#
#   .\scripts\run_email_cycle.ps1

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$py = ".\.venv\Scripts\python.exe"

$env:DB_PATH = "autoextract.email.db"
$env:DOMAIN  = "support_email"
$env:JSON_MODE = "1"
$env:ENRICH = "1"

function Log($m) { Write-Host "[email $(Get-Date -Format HH:mm:ss)] $m" -ForegroundColor Cyan }

Log "waiting for email generation to finish..."
while ($true) {
    if (Test-Path "logs\gen_emails.log") {
        $c = Get-Content "logs\gen_emails.log" -Raw -ErrorAction SilentlyContinue
        if ($c -match "Done\.") { Log "generation complete"; break }
        if ($c -match "Traceback") { Log "generation FAILED"; exit 1 }
    }
    Start-Sleep -Seconds 20
}

Log "freezing the email holdout"
& $py scripts\freeze_eval.py 2>&1 | Tee-Object -FilePath logs\email_freeze.log

Log "scoring the baseline"
& $py scripts\run_cycle.py --stage baseline *>&1 | Out-File logs\email_baseline.log

Log "serving live emails"
& $py scripts\run_cycle.py --stage serve --n 600 --workers 5 *>&1 | Out-File logs\email_serve.log

Log "repairing failures"
& $py scripts\run_cycle.py --stage repair --limit 700 --workers 5 *>&1 | Out-File logs\email_repair.log

Log "clusters"
& $py scripts\run_cycle.py --stage clusters *>&1 | Out-File logs\email_clusters.log

Log "building a training dataset"
& $py -c "from train.dataset import build_dataset; import json; m=build_dataset(max_examples=1200); print(json.dumps({k:v for k,v in m.items() if k!='signatures'}, indent=2))" *>&1 |
    Out-File logs\email_dataset.log

Log "EMAIL CYCLE COMPLETE"
Get-Content logs\email_baseline.log, logs\email_serve.log, logs\email_repair.log |
    Select-String -Pattern "valid_rate|field_f1|processed|verified"
