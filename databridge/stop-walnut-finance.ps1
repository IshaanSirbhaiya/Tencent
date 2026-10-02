# ─────────────────────────────────────────────────────────────────────────────
# stop-walnut-finance.ps1  —  stop the FINANCE live demo. Touches nothing of the workspace demo:
#                             never ports 8000/3000, never the containers, never var\ outside
#                             var\finance\.
#
#   .\stop-walnut-finance.ps1              free ports 8100 + 3100 (state and database kept)
#   .\stop-walnut-finance.ps1 -WipeState   also delete the finance state in var\finance\ (graph,
#                                          sources, plans, runs, agents, approval key); the built
#                                          frontend copy is kept, so the next start is a fresh graph
#   .\stop-walnut-finance.ps1 -DropDb      also drop the finance_demo database and its two roles
#                                          (the Larkspur database and roles are not touched)
# ─────────────────────────────────────────────────────────────────────────────
param([switch]$WipeState, [switch]$DropDb)
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot

function Free-Port($port) {
  $found = $false
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host ("  stopping PID {0} on port {1}" -f $_.OwningProcess, $port) -ForegroundColor Yellow
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    $found = $true
  }
  if (-not $found) { Write-Host ("  port {0} already free" -f $port) -ForegroundColor DarkGray }
}

Write-Host "Stopping the Walnut FINANCE demo (ports 8100 + 3100 only)..." -ForegroundColor Cyan
Free-Port 8100
Free-Port 3100

if ($DropDb) {
  Write-Host "Dropping database finance_demo and roles finance_reader / finance_agent_writer..." -ForegroundColor Yellow
  "DROP DATABASE IF EXISTS finance_demo WITH (FORCE); DROP ROLE IF EXISTS finance_reader; DROP ROLE IF EXISTS finance_agent_writer;" |
    docker exec -i walnut_demo_pg psql -U walnut -d postgres -v ON_ERROR_STOP=1 -X -q
}
if ($WipeState) {
  # Deletes the finance STATE (graph, sources, plans, runs, agents, captures, approval key, logs).
  # The built frontend copy (var\finance\frontend) is kept so the next start does not rebuild it.
  $fin = Join-Path $root "var\finance"
  Start-Sleep -Seconds 1
  Get-ChildItem $fin -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne "frontend" } | ForEach-Object {
    Write-Host ("  deleting {0}" -f $_.FullName) -ForegroundColor Yellow
    Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue
  }
}
Write-Host "Done." -ForegroundColor Green
