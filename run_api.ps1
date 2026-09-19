# Start the API. Refuses to start if port 8000 is already held.
#
# Why the preflight matters: this machine's GLOBAL python also has fastapi and
# uvicorn installed, so a stray `python -m uvicorn` binds :8000 from a
# different interpreter. The new server then fails to bind, the old one keeps
# answering, and you spend twenty minutes wondering why your new routes 404.
# Fail loudly instead.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "venv not found at $py" -ForegroundColor Red
    Write-Host "run:  uv venv --python 3.13; uv pip install -e `".[dev]`"" -ForegroundColor Yellow
    exit 1
}

$held = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($held) {
    Write-Host "PORT 8000 IS ALREADY IN USE" -ForegroundColor Red
    foreach ($c in $held) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)" -ErrorAction SilentlyContinue
        Write-Host "  PID $($c.OwningProcess)  $($p.CommandLine)" -ForegroundColor Yellow
    }
    Write-Host "`nkill it with:" -ForegroundColor Yellow
    Write-Host "  Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id `$_.OwningProcess -Force }"
    exit 1
}

Set-Location $root
Write-Host "interpreter : $py" -ForegroundColor DarkGray
Write-Host "API         : http://127.0.0.1:8000/docs" -ForegroundColor Green
Write-Host "(use 127.0.0.1, not localhost - localhost costs ~2s per request on Windows)" -ForegroundColor DarkGray
Write-Host ""

& $py -m uvicorn api.main:app --port 8000 --reload
