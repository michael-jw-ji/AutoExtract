# Verifies everything DEMO.md depends on, before you walk up.
#
# Checks the two services, the example files, the database rows the script
# points at, and -- the one that actually bit us -- that the corpus is real
# documents rather than fragments.
#
#   .\scripts\preflight.ps1

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$py = ".\.venv\Scripts\python.exe"

$fail = 0
function Ok($m)   { Write-Host "  [ok]   $m" -ForegroundColor Green }
function Bad($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red; $script:fail++ }
function Warn($m) { Write-Host "  [warn] $m" -ForegroundColor Yellow }

Write-Host "`nPREFLIGHT" -ForegroundColor Cyan
Write-Host ("-" * 60)

# DB_PATH must be unset: the demo reads autoextract.db, and a leftover
# DB_PATH from an experiment silently points the dashboard at another corpus.
if ($env:DB_PATH) { Bad "DB_PATH is set to '$env:DB_PATH' - unset it (`$env:DB_PATH=`$null)" }
else { Ok "DB_PATH unset (demo reads autoextract.db)" }
if ($env:DOMAIN -and $env:DOMAIN -ne "invoice") { Bad "DOMAIN is '$env:DOMAIN' - the demo wants invoice" }
else { Ok "DOMAIN is invoice" }

Write-Host "`nSERVICES"
try {
    $r = Invoke-WebRequest "http://127.0.0.1:8000/api/stats" -TimeoutSec 5 -UseBasicParsing
    $s = $r.Content | ConvertFrom-Json
    Ok "api :8000 responding - $($s.extractions) extractions, valid $([math]::Round($s.valid_rate*100,1))%"
} catch { Bad "api :8000 unreachable - run .\run_api.ps1" }

try {
    Invoke-WebRequest "http://127.0.0.1:3000" -TimeoutSec 8 -UseBasicParsing | Out-Null
    Ok "dashboard :3000 responding"
} catch { Bad "dashboard :3000 unreachable - run .\run_dashboard.ps1" }

Write-Host "`nEXAMPLE FILES"
foreach ($n in @("invoice_missing_totals", "invoice_currency_symbols", "invoice_scanned_fax")) {
    $txt = "examples\$n.txt"; $gold = "examples\$n.gold.json"
    if ((Test-Path $txt) -and (Test-Path $gold)) {
        $len = (Get-Item $txt).Length
        if ($len -lt 200) { Bad "$n.txt is only $len bytes - that is a fragment, not a document" }
        else { Ok "$n.txt ($len bytes) + gold" }
    } else { Bad "$n missing its .txt or .gold.json" }
}

Write-Host "`nDATABASE"
$q = @'
import sqlite3, sys
con = sqlite3.connect("autoextract.db"); con.row_factory = sqlite3.Row
def n(sql): return con.execute(sql).fetchone()[0]
print("docs", n("SELECT COUNT(*) FROM documents"))
print("extractions", n("SELECT COUNT(*) FROM extractions"))
print("versions", n("SELECT COUNT(*) FROM model_versions"))
print("promoted", n("SELECT COUNT(*) FROM promotions WHERE decision='promoted'"))
print("rejected", n("SELECT COUNT(*) FROM promotions WHERE decision='rejected'"))
print("shortest", n("SELECT MIN(LENGTH(text)) FROM documents"))
'@
$out = $q | & $py -
$vals = @{}
foreach ($line in $out) { $p = $line -split " "; $vals[$p[0]] = [int]$p[1] }

if ($vals.docs -gt 0)        { Ok "documents: $($vals.docs)" }          else { Bad "no documents" }
if ($vals.extractions -gt 0) { Ok "extractions: $($vals.extractions)" } else { Bad "no extractions" }
if ($vals.versions -ge 2)    { Ok "model versions: $($vals.versions)" } else { Bad "need >=2 model versions for the lineage table" }

# The rejection is the beat that proves the gate is real. Without it the demo
# is just "number went up".
if ($vals.rejected -ge 1) { Ok "rejected retrains: $($vals.rejected)  <- the 2:20 beat" }
else { Bad "NO rejected retrain in history - the gate beat has nothing to point at" }
if ($vals.promoted -ge 1) { Ok "promoted: $($vals.promoted)" } else { Bad "no promotions" }

# The corpus-fragment check. A JSON-mode leak once reduced an entire corpus to
# 9-character fragments, and everything downstream still "ran".
if ($vals.shortest -ge 200) { Ok "shortest document: $($vals.shortest) chars" }
else { Bad "shortest document is $($vals.shortest) chars - corpus contains fragments" }

Write-Host "`nSNAPSHOTS (the 2:40 beat)"
foreach ($n in @("hosted-small", "cheap-lora", "local-base", "local-lora", "local-control")) {
    if (Test-Path "data\snapshots\$n.json") { Ok "$n.json" } else { Warn "$n.json missing" }
}

Write-Host "`nLIVE EXTRACTION (the 0:00 beat)"
$t0 = Get-Date
& $py scripts\demo_case.py examples\invoice_missing_totals.txt --raw *> "$env:TEMP\preflight_demo.txt"
$ms = [int]((Get-Date) - $t0).TotalMilliseconds
$body = Get-Content "$env:TEMP\preflight_demo.txt" -Raw
if ($body -match "INVALID") { Ok "demo_case --raw returns INVALID as scripted ($ms ms)" }
else { Bad "demo_case --raw did NOT return INVALID - the opening beat is broken" }
if ($body -match "VND-01530") { Ok "registry error message present" } else { Warn "expected vendor error text not found" }

Write-Host ("-" * 60)
if ($fail -eq 0) { Write-Host "READY" -ForegroundColor Green }
else { Write-Host "$fail CHECK(S) FAILED - fix before demoing" -ForegroundColor Red; exit 1 }
