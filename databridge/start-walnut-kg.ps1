# ─────────────────────────────────────────────────────────────────────────────
# start-walnut-kg.ps1  —  Walnut in knowledge-graph mode (MCP-ingested workspace)
#
#   Run from PowerShell:   .\start-walnut-kg.ps1
#   Stop:                  .\stop-walnut-kg.ps1        (add -Down to also remove the containers)
#
# What it does, in order:
#   1. Binds the `workspace` pack for this run only (DATABRIDGE_PACKS=workspace). The checked-in
#      packs/deployment.yaml is NOT changed.
#   2. Points the gateway at ONE shared graph store and the ingest folders under var/ (gitignored).
#   3. Starts the demo infrastructure with Docker (synthetic Postgres on 5433, MCPJungle on 8080)
#      if Docker Desktop is running. If it is not, it says so and carries on — the app still
#      starts, it just has no database / MCP gateway to connect to.
#   4. Frees ports 8000 + 3000 (like start-databridge.ps1) and starts the gateway and the
#      frontend in their own windows. Builds the frontend first if it has never been built.
# ─────────────────────────────────────────────────────────────────────────────
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$compose = Join-Path $root "infra\docker-compose.walnut.yml"

function Free-Port($port) {
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host ("  freeing port {0} (stopping PID {1})" -f $port, $_.OwningProcess) -ForegroundColor Yellow
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
  }
}

# ── 1 + 2. environment for this run (inherited by the two child windows) ─────────────────────
$env:WALNUT_ROOT            = $root
$env:DATABRIDGE_PACKS       = "workspace"
$env:WALNUT_GRAPH_DB_PATH   = "var/graph/workspace.db"
$env:WALNUT_CAPTURE_DIR     = "var/capture"
$env:WALNUT_SOURCES_DIR     = "var/sources"
$env:WALNUT_MCP_SERVERS     = "infra/mcp_servers.yaml"
# Agents + automation (BUILD_PLAN §5.3, §5.6, §5.7). The purpose and golden set are the governance
# half's deployment defaults an English sentence cannot supply; `compile_agent` refuses without
# them. An existing value in the calling shell wins.
if (-not $env:WALNUT_AGENT_PURPOSE)    { $env:WALNUT_AGENT_PURPOSE    = "workspace.agent_run" }
if (-not $env:WALNUT_AGENT_GOLDEN_SET) { $env:WALNUT_AGENT_GOLDEN_SET = "packs/workspace/evals/agent_contract.jsonl" }
if (-not $env:WALNUT_EVENTS_PATH)      { $env:WALNUT_EVENTS_PATH      = "var/events/events.jsonl" }
# Schedules written in English ("every weekday at 9") need a timezone; without one the compiler asks
# every time (R-017-t03). Deployment default; a timezone named in the sentence still wins.
if (-not $env:WALNUT_TIMEZONE)         { $env:WALNUT_TIMEZONE         = "Asia/Singapore" }
if (-not $env:WALNUT_RUNS_DIR)         { $env:WALNUT_RUNS_DIR         = "var/runs" }
if (-not $env:WALNUT_PASSAGES_DIR)     { $env:WALNUT_PASSAGES_DIR     = "var/passages" }
# The gateway hosts the trigger engine (schedules + event triggers) in its lifespan.
if (-not $env:WALNUT_AUTOMATION_ENGINE) { $env:WALNUT_AUTOMATION_ENGINE = "1" }
if (-not $env:MCPJUNGLE_URL)       { $env:MCPJUNGLE_URL = "http://127.0.0.1:8080" }
if (-not $env:WALNUT_DEMO_PG_PORT) { $env:WALNUT_DEMO_PG_PORT = "5433" }
# R-022 round 2 / E2E run 2 N-10: infra/mcp_servers.yaml references the demo DB passwords as plain
# ${VAR} (a defaulted credential is refused by the secret scan). The SYNTHETIC demo values are set
# here, for the local demo only.
if (-not $env:WALNUT_DEMO_PG_READER_PASSWORD) { $env:WALNUT_DEMO_PG_READER_PASSWORD = "walnut_reader_synthetic" }
if (-not $env:WALNUT_DEMO_PG_WRITER_PASSWORD) { $env:WALNUT_DEMO_PG_WRITER_PASSWORD = "walnut_agent_writer_synthetic" }
# R-022 round 2 (Codex f2 #10): source registration is fail-closed unless the DEPLOYMENT declares
# operator mode; this local launcher is the host operator.
if (-not $env:WALNUT_SOURCE_REGISTRATION) { $env:WALNUT_SOURCE_REGISTRATION = "operator" }
# INT5 (R-022 build cross-track 4): approvals of screen overrides are SEALED with an HMAC under the
# deployment approval key (mcp_ingest/screen.py). For this LOCAL demo the key is a file under var/
# (gitignored; it is NEVER committed), generated here with 256 random bits if it does not exist yet.
# It must live OUTSIDE the sources folder (var/sources): whoever can append an approval line must not
# also be able to read the key that seals it. An existing WALNUT_APPROVAL_KEY or _KEY_FILE in the
# calling shell wins. Who holds and rotates the key in a real deployment is a founder decision
# (R-022 founder decision 1). Deleting this file voids every sealed approval made with it.
if (-not $env:WALNUT_APPROVAL_KEY -and -not $env:WALNUT_APPROVAL_KEY_FILE) {
  $env:WALNUT_APPROVAL_KEY_FILE = Join-Path $root "var\keys\approval.key"
}
if (-not $env:WALNUT_APPROVAL_KEY -and -not (Test-Path $env:WALNUT_APPROVAL_KEY_FILE)) {
  New-Item -ItemType Directory -Force (Split-Path $env:WALNUT_APPROVAL_KEY_FILE) | Out-Null
  $bytes = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $hex = -join ($bytes | ForEach-Object { $_.ToString("x2") })
  [System.IO.File]::WriteAllText($env:WALNUT_APPROVAL_KEY_FILE, $hex)
  Write-Host "  generated a local approval key: $env:WALNUT_APPROVAL_KEY_FILE (gitignored)" -ForegroundColor DarkGray
}
$env:PYTHONUNBUFFERED       = "1"

foreach ($d in @("var\graph", "var\capture", "var\sources", "var\ingest", "var\events", "var\runs", "var\passages", "var\agents", "var\adapter_agents", "var\secrets\gdrive")) {
  New-Item -ItemType Directory -Force (Join-Path $root $d) | Out-Null
}

Write-Host "Walnut knowledge-graph mode" -ForegroundColor Cyan
Write-Host "  packs         : $env:DATABRIDGE_PACKS  (packs/deployment.yaml unchanged)" -ForegroundColor DarkGray
Write-Host "  graph store   : $env:WALNUT_GRAPH_DB_PATH" -ForegroundColor DarkGray
Write-Host "  MCP servers   : $env:WALNUT_MCP_SERVERS" -ForegroundColor DarkGray
Write-Host "  agent purpose : $env:WALNUT_AGENT_PURPOSE  (golden set $env:WALNUT_AGENT_GOLDEN_SET)" -ForegroundColor DarkGray
Write-Host "  runs / events : $env:WALNUT_RUNS_DIR  /  $env:WALNUT_EVENTS_PATH  (trigger engine: $env:WALNUT_AUTOMATION_ENGINE)" -ForegroundColor DarkGray

# ── 3. demo infrastructure ──────────────────────────────────────────────────────────────────
$dockerOk = $false
if (Get-Command docker -ErrorAction SilentlyContinue) {
  docker info --format "{{.ServerVersion}}" *> $null
  if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
}
if ($dockerOk) {
  Write-Host "Starting demo Postgres (5433) + MCPJungle (8080) with Docker..." -ForegroundColor Cyan
  docker compose -f $compose up -d
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  docker compose failed (see above). Is something else using port 5433 or 8080?" -ForegroundColor Red
  } else {
    $healthy = $false
    for ($i = 0; $i -lt 30; $i++) {
      try {
        $r = Invoke-RestMethod -Uri "$env:MCPJUNGLE_URL/health" -TimeoutSec 2
        if ($r.status -eq "ok") { $healthy = $true; break }
      } catch { }
      Start-Sleep -Seconds 2
    }
    if ($healthy) {
      Write-Host "  MCPJungle is up at $env:MCPJUNGLE_URL" -ForegroundColor Green
    } else {
      Write-Host "  MCPJungle did not answer /health within 60s. Check: docker compose -f infra\docker-compose.walnut.yml logs mcpjungle" -ForegroundColor Yellow
    }
    Write-Host "  Demo database: postgresql://walnut_reader:walnut_reader_synthetic@127.0.0.1:$env:WALNUT_DEMO_PG_PORT/larkspur (synthetic)" -ForegroundColor DarkGray
  }
} else {
  Write-Host ""
  Write-Host "Docker is not running, so the demo database and the MCP gateway were NOT started." -ForegroundColor Yellow
  Write-Host "  To get them: open Docker Desktop, wait until it says 'Engine running', then run this script again." -ForegroundColor Yellow
  Write-Host "  Walnut itself will still start; connecting a local folder directly (npx) still works." -ForegroundColor Yellow
  Write-Host ""
}

# ── 4. gateway + frontend ───────────────────────────────────────────────────────────────────
Write-Host "Freeing ports 8000 + 3000..." -ForegroundColor Cyan
Free-Port 8000
Free-Port 3000
Start-Sleep -Milliseconds 600

# NEXT_PUBLIC_* is inlined at BUILD time — set before any build (see start-databridge.ps1).
$env:NEXT_PUBLIC_WALNUT_TOKEN = "dev-local-only"

if (-not (Test-Path "$root\apps\frontend\.next")) {
  Write-Host "First run: building the frontend (npm run build)..." -ForegroundColor Cyan
  Push-Location "$root\apps\frontend"; npm run build; Pop-Location
}

# R-015: local-demo auth token for the gateway process only (defaults OFF everywhere else).
Write-Host "Enabling the local-demo auth token (WALNUT_DEV_AUTH=1) for the gateway process..." -ForegroundColor Yellow
$env:WALNUT_DEV_AUTH = "1"

Write-Host "Starting gateway (backend, port 8000)..." -ForegroundColor Green
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root'; python apps\gateway\serve.py"

Write-Host "Starting frontend (port 3000)..." -ForegroundColor Green
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root\apps\frontend'; npm run start"

Write-Host ""
Write-Host "Walnut (knowledge-graph mode) is starting in two new windows. Give it ~10 seconds, then open:" -ForegroundColor Cyan
Write-Host "    http://localhost:3000/graph" -ForegroundColor White
Write-Host "Gateway health:   http://127.0.0.1:8000/health" -ForegroundColor DarkGray
Write-Host "MCPJungle:        $env:MCPJUNGLE_URL/health" -ForegroundColor DarkGray
Write-Host "Google Drive:     see docs\GOOGLE_DRIVE_SETUP.md" -ForegroundColor DarkGray
