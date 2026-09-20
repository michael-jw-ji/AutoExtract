# Re-drains the email failure buffer after the subject-normalisation fix.
#
# subject blocked 94% of email repairs from verifying: the rendered emails
# carry "Re: " prefixes and a "[TKT-...]" suffix that the constructed gold
# subject does not, so a model that read the line perfectly still failed an
# exact match. domains/support_email.py::normalize_field now strips exactly
# that noise before comparison -- and nothing else.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$py = ".\.venv\Scripts\python.exe"
$env:DB_PATH = "autoextract.email.db"
$env:DOMAIN  = "support_email"
$env:JSON_MODE = "1"
$env:ENRICH = "1"

function Log($m) { Write-Host "[rerepair $(Get-Date -Format HH:mm:ss)] $m" }

Log "re-repairing with subject normalisation (workers 3, to stay under the rate limit)"
& $py scripts\run_cycle.py --stage repair --limit 700 --workers 3 *>&1 |
    Out-File logs\email_repair4.log

Log "rebuilding the dataset"
& $py -c "from train.dataset import build_dataset; import json; m=build_dataset(max_examples=1200); print(json.dumps({k:v for k,v in m.items() if k!='signatures'}, indent=2))" *>&1 |
    Out-File logs\email_dataset4.log

Log "re-scoring the baseline (workers 3)"
& $py scripts\run_cycle.py --stage baseline *>&1 | Out-File logs\email_baseline4.log

Log "EMAIL REREPAIR COMPLETE"
Get-Content logs\email_repair4.log, logs\email_dataset4.log, logs\email_baseline4.log |
    Select-String -Pattern "verified|repair_count|replay_count|valid_rate|field_f1"
