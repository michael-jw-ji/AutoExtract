# Rebuilds the support_email corpus from scratch, after the generator fix.
#
# The old corpus was unusable: generation inherited JSON_MODE=1 from the
# environment, so the RENDERER was forced into JSON mode and returned
# fragments like `[2026.08, "a plain business email"]` instead of emails.
# Every document was 6-12 characters. Nothing downstream could work.
#
# scripts/gen_emails.py now passes json_mode=False explicitly and drops any
# render that is short, JSON-shaped, or missing the values it was told to
# include. A smaller honest corpus beats a larger contaminated one.
#
#   .\scripts\rebuild_email.ps1

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$py = ".\.venv\Scripts\python.exe"

$env:DB_PATH   = "autoextract.email.db"
$env:DOMAIN    = "support_email"
$env:JSON_MODE = "1"
$env:ENRICH    = "1"

function Log($m) { Write-Host "[rebuild $(Get-Date -Format HH:mm:ss)] $m" }

# The old database is contaminated end to end -- documents, extractions,
# failures and the frozen holdout hash all derive from fragments. Keep it
# aside rather than deleting, so the before/after is inspectable.
if (Test-Path "autoextract.email.db") {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Move-Item "autoextract.email.db" "data\autoextract.email.contaminated-$stamp.db" -Force
    Log "moved the contaminated db aside"
}

Log "generating 600 live + 200 holdout emails"
& $py scripts\gen_emails.py --live 600 --holdout 200 --workers 5 *>&1 |
    Out-File logs\gen_emails2.log

# Corpus hygiene gate. If generation dropped a lot, the run is not worth
# building on -- say so loudly instead of quietly continuing.
$live = & $py -c "import sqlite3;print(sqlite3.connect('autoextract.email.db').execute(\"SELECT COUNT(*) FROM documents WHERE split='live'\").fetchone()[0])"
$hold = & $py -c "import sqlite3;print(sqlite3.connect('autoextract.email.db').execute(\"SELECT COUNT(*) FROM documents WHERE split='holdout'\").fetchone()[0])"
$shortest = & $py -c "import sqlite3;print(min(len(r[0]) for r in sqlite3.connect('autoextract.email.db').execute('SELECT text FROM documents')))"
Log "generated live=$live holdout=$hold shortest-document=$shortest chars"
if ([int]$shortest -lt 120) { Log "ABORT: a fragment survived the guard"; exit 1 }

Log "freezing the holdout"
& $py scripts\freeze_eval.py *>&1 | Tee-Object -FilePath logs\email_freeze2.log

Log "scoring the baseline"
& $py scripts\run_cycle.py --stage baseline *>&1 | Out-File logs\email_baseline3.log

Log "serving live emails"
& $py scripts\run_cycle.py --stage serve --n 600 --workers 5 *>&1 | Out-File logs\email_serve3.log

Log "repairing failures"
& $py scripts\run_cycle.py --stage repair --limit 700 --workers 5 *>&1 | Out-File logs\email_repair3.log

Log "clusters"
& $py scripts\run_cycle.py --stage clusters *>&1 | Out-File logs\email_clusters3.log

Log "building a training dataset"
& $py -c "from train.dataset import build_dataset; import json; m=build_dataset(max_examples=1200); print(json.dumps({k:v for k,v in m.items() if k!='signatures'}, indent=2))" *>&1 |
    Out-File logs\email_dataset3.log

Log "EMAIL REBUILD COMPLETE"
Get-Content logs\email_baseline3.log, logs\email_serve3.log, logs\email_repair3.log, logs\email_dataset3.log |
    Select-String -Pattern "valid_rate|field_f1|processed|verified|repairs|replay"
