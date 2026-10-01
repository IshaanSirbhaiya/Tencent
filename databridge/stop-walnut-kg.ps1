# ─────────────────────────────────────────────────────────────────────────────
# stop-walnut-kg.ps1  —  stop Walnut knowledge-graph mode
#
#   .\stop-walnut-kg.ps1          free ports 8000 + 3000 and STOP the demo containers
#                                 (data kept; the next start is instant)
#   .\stop-walnut-kg.ps1 -Down    also REMOVE the containers (volumes kept)
#   .\stop-walnut-kg.ps1 -Down -Wipe
#                                 also delete the demo volumes: Postgres is re-seeded from
#                                 fixtures/workspace_demo/db and MCPJungle forgets registrations
#
# Never touches var/ (graph, captures, Google sign-in token).
# ─────────────────────────────────────────────────────────────────────────────
param([switch]$Down, [switch]$Wipe)
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$compose = Join-Path $root "infra\docker-compose.walnut.yml"

function Free-Port($port) {
  $found = $false
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host ("  stopping PID {0} on port {1}" -f $_.OwningProcess, $port) -ForegroundColor Yellow
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    $found = $true
  }
  if (-not $found) { Write-Host ("  port {0} already free" -f $port) -ForegroundColor DarkGray }
}

Write-Host "Stopping Walnut (freeing ports 8000 + 3000)..." -ForegroundColor Cyan
Free-Port 8000
Free-Port 3000

$dockerOk = $false
if (Get-Command docker -ErrorAction SilentlyContinue) {
  docker info --format "{{.ServerVersion}}" *> $null
  if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
}
if (-not $dockerOk) {
  Write-Host "Docker is not running; no containers to stop." -ForegroundColor DarkGray
} elseif ($Down) {
  if ($Wipe) {
    Write-Host "Removing demo containers AND their volumes..." -ForegroundColor Yellow
    docker compose -f $compose down -v
  } else {
    Write-Host "Removing demo containers (volumes kept)..." -ForegroundColor Cyan
    docker compose -f $compose down
  }
} else {
  Write-Host "Stopping demo containers (data kept)..." -ForegroundColor Cyan
  docker compose -f $compose stop
}
Write-Host "Done." -ForegroundColor Green
