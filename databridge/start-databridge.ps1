# ─────────────────────────────────────────────────────────────────────────────
# start-databridge.ps1  —  one-command, collision-proof launcher
#
#   Run from PowerShell:   .\start-databridge.ps1
#
# It FREES ports 8000 (gateway) and 3000 (frontend) first — killing whatever is on
# them (e.g. the AgentInfra static server) — then starts both DataBridge servers in
# their own windows. So you never get the "[Errno 10048] port already in use" error
# again: this script always wins the ports.
#
# To switch back to AgentInfra: run .\stop-databridge.ps1 (frees the ports), then
# start AgentInfra as usual.
# ─────────────────────────────────────────────────────────────────────────────
$ErrorActionPreference = "SilentlyContinue"
$root = $PSScriptRoot

function Free-Port($port) {
  Get-NetTCPConnection -LocalPort $port -State Listen | ForEach-Object {
    Write-Host ("  freeing port {0} (stopping PID {1})" -f $port, $_.OwningProcess) -ForegroundColor Yellow
    Stop-Process -Id $_.OwningProcess -Force
  }
}

Write-Host "Freeing ports 8000 + 3000..." -ForegroundColor Cyan
Free-Port 8000
Free-Port 3000
Start-Sleep -Milliseconds 600

# build the frontend if it has never been built (first run)
if (-not (Test-Path "$root\apps\frontend\.next")) {
  Write-Host "First run: building the frontend (npm run build)..." -ForegroundColor Cyan
  Push-Location "$root\apps\frontend"; npm run build; Pop-Location
}

Write-Host "Starting gateway (backend, port 8000)..." -ForegroundColor Green
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root'; python apps\gateway\serve.py"

Write-Host "Starting frontend (port 3000)..." -ForegroundColor Green
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root\apps\frontend'; npm run start"

Write-Host ""
Write-Host "DataBridge is starting in two new windows. Give it ~10 seconds, then open:" -ForegroundColor Cyan
Write-Host "    http://localhost:3000/brain" -ForegroundColor White
Write-Host "Gateway health:  http://127.0.0.1:8000/health  (should say llm_backend: openai)" -ForegroundColor DarkGray
