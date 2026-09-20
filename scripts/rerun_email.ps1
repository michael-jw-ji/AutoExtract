# Re-runs the email track after the domain-blindness fixes.
#
# Two bugs made the first run report nothing:
#   verify/compare.py    scored invoice fields against email gold -> f1 0.0000
#                        AND every repair failed matches_gold()
#   repair/mechanical.py invoice-only allow-list wiped email payloads to {}
#
# Both are fixed, so this re-scores the baseline and re-drains the buffer.
# The documents and the frozen holdout are untouched; only the measurement
# and the repair path changed.
#
#   .\scripts\rerun_email.ps1

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$py = ".\.venv\Scripts\python.exe"

$env:DB_PATH   = "autoextract.email.db"
$env:DOMAIN    = "support_email"
$env:JSON_MODE = "1"
$env:ENRICH    = "1"

function Log($m) { Write-Host "[rerun $(Get-Date -Format HH:mm:ss)] $m" }

Log "re-scoring the baseline with domain-aware field comparison"
& $py scripts\run_cycle.py --stage baseline *>&1 | Out-File logs\email_baseline2.log

Log "re-repairing the 574 buffered failures"
& $py scripts\run_cycle.py --stage repair --limit 700 --workers 5 *>&1 |
    Out-File logs\email_repair2.log

Log "clusters"
& $py scripts\run_cycle.py --stage clusters *>&1 | Out-File logs\email_clusters2.log

Log "building a training dataset"
& $py -c "from train.dataset import build_dataset; import json; m=build_dataset(max_examples=1200); print(json.dumps({k:v for k,v in m.items() if k!='signatures'}, indent=2))" *>&1 |
    Out-File logs\email_dataset2.log

Log "EMAIL RERUN COMPLETE"
Get-Content logs\email_baseline2.log, logs\email_repair2.log, logs\email_dataset2.log |
    Select-String -Pattern "valid_rate|field_f1|processed|verified|repairs|replay"
