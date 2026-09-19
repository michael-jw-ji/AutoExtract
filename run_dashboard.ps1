# Start the dashboard. Refuses to start if port 3000 is already held.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$held = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
if ($held) {
    Write-Host "PORT 3000 IS ALREADY IN USE" -ForegroundColor Red
    foreach ($c in $held) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)" -ErrorAction SilentlyContinue
        Write-Host "  PID $($c.OwningProcess)  $($p.Name)" -ForegroundColor Yellow
    }
    exit 1
}

Set-Location (Join-Path $root "dashboard")
if (-not (Test-Path "node_modules")) {
    Write-Host "installing dependencies..." -ForegroundColor Yellow
    npm install --no-audit --no-fund
}

Write-Host "DASHBOARD : http://localhost:3000" -ForegroundColor Cyan
Write-Host ""
npm run dev
