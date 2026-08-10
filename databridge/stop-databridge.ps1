# ─────────────────────────────────────────────────────────────────────────────
# stop-databridge.ps1  —  free ports 8000 + 3000 (stop DataBridge cleanly)
#
#   Run from PowerShell:   .\stop-databridge.ps1
#
# Use this before launching AgentInfra (or anything else) on those ports, so the
# two projects never collide. It only touches whatever is LISTENING on 8000/3000.
# ─────────────────────────────────────────────────────────────────────────────
$ErrorActionPreference = "SilentlyContinue"

function Free-Port($port) {
  $found = $false
  Get-NetTCPConnection -LocalPort $port -State Listen | ForEach-Object {
    Write-Host ("  stopping PID {0} on port {1}" -f $_.OwningProcess, $port) -ForegroundColor Yellow
    Stop-Process -Id $_.OwningProcess -Force
    $found = $true
  }
  if (-not $found) { Write-Host ("  port {0} already free" -f $port) -ForegroundColor DarkGray }
}

Write-Host "Stopping DataBridge (freeing ports 8000 + 3000)..." -ForegroundColor Cyan
Free-Port 8000
Free-Port 3000
Write-Host "Done. Ports 3000 and 8000 are now free." -ForegroundColor Green
