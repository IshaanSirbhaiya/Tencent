# ─────────────────────────────────────────────────────────────────────────────
# start-walnut-finance.ps1  —  Walnut on the SYNTHETIC finance estate, side by side with the
#                              workspace demo (start-walnut-kg.ps1). Nothing of the workspace
#                              demo is touched: other ports, other state folder, other database.
#
#   Run from PowerShell (in databridge\):   .\start-walnut-finance.ps1
#   Rebuild the finance frontend copy:       .\start-walnut-finance.ps1 -RebuildFrontend
#   Stop:                                    .\stop-walnut-finance.ps1
#
#   Finance frontend : http://localhost:3100        (workspace demo stays on :3000)
#   Finance gateway  : http://127.0.0.1:8100/health (workspace demo stays on :8000)
#   State folder     : var\finance\  (graph, sources, runs, agents, approval key — gitignored)
#   Database         : finance_demo inside the walnut_demo_pg container (:5433), role finance_reader
#   MCP manifest     : infra\mcp_servers.finance.yaml
#
# What it does, in order:
#   1. Sets this run's environment UNCONDITIONALLY (so a shell that ran start-walnut-kg.ps1 first
#      cannot leak the workspace paths into the finance process): DATABRIDGE_PACKS=finance and
#      every WALNUT_* location under var\finance\. packs\deployment.yaml is NOT changed.
#   2. Makes sure the demo Postgres container is up and that finance_demo is loaded (the loader is
#      idempotent: it loads once, then only verifies).
#   3. Keeps a private copy of the frontend in var\finance\frontend (Next inlines the gateway URL
#      and token at BUILD time, so the :3000 build cannot be reused, and rebuilding it in place
#      would change the workspace demo). node_modules is a junction to the real one.
#   4. Frees ONLY ports 8100 + 3100 and starts the finance gateway and frontend in two windows.
#      Each window also writes its log to var\finance\logs\.
# ─────────────────────────────────────────────────────────────────────────────
param([switch]$RebuildFrontend)
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$fin = Join-Path $root "var\finance"
$gatewayPort = 8100
$frontendPort = 3100

function Free-Port($port) {
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host ("  freeing port {0} (stopping PID {1})" -f $port, $_.OwningProcess) -ForegroundColor Yellow
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
  }
}

# ── 1. environment (inherited by the two child windows) ──────────────────────────────────────
foreach ($d in @("graph", "capture", "sources", "ingest", "events\offsets", "runs", "passages", "cache",
                 "agents", "adapter_agents", "automation", "facts", "ontology", "mcp_state",
                 "materialise", "keys", "followups", "logs")) {
  New-Item -ItemType Directory -Force (Join-Path $fin $d) | Out-Null
}
$env:WALNUT_ROOT                = $root
$env:WALNUT_PYTHON              = (Get-Command python).Source
$env:DATABRIDGE_PACKS           = "finance"
$env:WALNUT_GRAPH_DB_PATH       = Join-Path $fin "graph\finance.db"
$env:WALNUT_CAPTURE_DIR         = Join-Path $fin "capture"
$env:WALNUT_SOURCES_DIR         = Join-Path $fin "sources"
$env:WALNUT_INGEST_DIR          = Join-Path $fin "ingest"
$env:WALNUT_EVENTS_PATH         = Join-Path $fin "events\events.jsonl"
$env:WALNUT_EVENTS_OFFSETS_DIR  = Join-Path $fin "events\offsets"
$env:WALNUT_RUNS_DIR            = Join-Path $fin "runs"
$env:WALNUT_PASSAGES_DIR        = Join-Path $fin "passages"
$env:WALNUT_CACHE_DIR           = Join-Path $fin "cache"
$env:WALNUT_AGENTS_DIR          = Join-Path $fin "agents"
$env:WALNUT_ADAPTER_AGENTS_DIR  = Join-Path $fin "adapter_agents"
$env:WALNUT_AUTOMATION_DIR      = Join-Path $fin "automation"
$env:WALNUT_FACTS_DIR           = Join-Path $fin "facts"
$env:WALNUT_ONTOLOGY_STATE_PATH = Join-Path $fin "ontology\state.json"
$env:WALNUT_ONTOLOGY_AUDIT_PATH = Join-Path $fin "ontology\audit.jsonl"
$env:WALNUT_ACT_REGISTRY_PATH   = Join-Path $fin "ontology\act_registry.json"
$env:WALNUT_MCP_STATE           = Join-Path $fin "mcp_state"
$env:WALNUT_MATERIALISE_DIR     = Join-Path $fin "materialise"
$env:DATABRIDGE_OUTBOX_DB       = Join-Path $fin "outbox.sqlite"
$env:WALNUT_MCP_SERVERS         = Join-Path $root "infra\mcp_servers.finance.yaml"
$env:WALNUT_MCP_DECLARATIONS    = $env:WALNUT_MCP_SERVERS
# Agents compiled from English need a purpose (the lawful basis of every read) and a golden set.
# The finance pack now declares its own general purpose (`finance.agent_run`, marked
# default_for_compiled_agents) and golden set (ANS6, R-026), closing the pack gap the dry run found.
$env:WALNUT_AGENT_PURPOSE       = "finance.agent_run"
$env:WALNUT_AGENT_GOLDEN_SET    = "packs/finance/evals/agent_contract.jsonl"
$env:WALNUT_TIMEZONE            = "Asia/Singapore"
$env:WALNUT_AUTOMATION_ENGINE   = "1"
# Index every built document into passages during ingest (document search). Without this, the text
# of a credit memo, filing or email is not searchable: /brain cannot quote it, an agent cannot read
# it from search, and a "retrieval corpus" dataset has no text to chunk (found in the FIN1 dry run).
$env:WALNUT_INGEST_PASSAGES     = "1"
$env:WALNUT_DEMO_PG_PORT        = "5433"
# SYNTHETIC demo passwords for the finance_demo roles (created by the loader below).
$env:WALNUT_FIN_PG_READER_PASSWORD = "finance_reader_synthetic"
$env:WALNUT_FIN_PG_WRITER_PASSWORD = "finance_agent_writer_synthetic"
# This local launcher is the host operator (sources are registered from the operator manifest).
$env:WALNUT_SOURCE_REGISTRATION = "operator"
# The finance deployment's OWN approval key (seals approvals). Never shared with the workspace demo.
Remove-Item Env:\WALNUT_APPROVAL_KEY -ErrorAction SilentlyContinue
$env:WALNUT_APPROVAL_KEY_FILE   = Join-Path $fin "keys\approval.key"
if (-not (Test-Path $env:WALNUT_APPROVAL_KEY_FILE)) {
  $bytes = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $hex = -join ($bytes | ForEach-Object { $_.ToString("x2") })
  [System.IO.File]::WriteAllText($env:WALNUT_APPROVAL_KEY_FILE, $hex)
  Write-Host "  generated the finance approval key: $env:WALNUT_APPROVAL_KEY_FILE (gitignored)" -ForegroundColor DarkGray
}
# Local-demo sign-in: the fixed dev token, for this gateway process only (as start-walnut-kg.ps1).
$env:WALNUT_DEV_AUTH            = "1"
$env:PYTHONUNBUFFERED           = "1"
$env:PYTHONIOENCODING           = "utf-8"

Write-Host "Walnut FINANCE live demo (synthetic estate)" -ForegroundColor Cyan
Write-Host "  pack          : finance  (packs\deployment.yaml unchanged)" -ForegroundColor DarkGray
Write-Host "  state         : $fin" -ForegroundColor DarkGray
Write-Host "  MCP manifest  : $env:WALNUT_MCP_SERVERS" -ForegroundColor DarkGray
Write-Host "  agent purpose : $env:WALNUT_AGENT_PURPOSE  (golden set $env:WALNUT_AGENT_GOLDEN_SET)" -ForegroundColor DarkGray

# ── 2. the database ─────────────────────────────────────────────────────────────────────────
$dockerOk = $false
if (Get-Command docker -ErrorAction SilentlyContinue) {
  docker info --format "{{.ServerVersion}}" *> $null
  if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
}
if (-not $dockerOk) {
  Write-Host "Docker is not running: open Docker Desktop, wait for 'Engine running', then run this again." -ForegroundColor Red
  Write-Host "  (The finance database lives in the walnut_demo_pg container.)" -ForegroundColor Red
  return
}
$state = docker inspect -f "{{.State.Running}}" walnut_demo_pg 2>$null
if ($state -ne "true") {
  Write-Host "Starting the demo Postgres container (walnut_demo_pg, :5433)..." -ForegroundColor Cyan
  docker compose -f (Join-Path $root "infra\docker-compose.walnut.yml") up -d walnut_demo_pg
  for ($i = 0; $i -lt 30; $i++) {
    docker exec walnut_demo_pg pg_isready -U walnut *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 2
  }
}
Write-Host "Checking the finance database (loads once, then only verifies)..." -ForegroundColor Cyan
python (Join-Path $root "scripts\finance_live\load_finance_db.py")
if ($LASTEXITCODE -ne 0) {
  Write-Host "  The finance database is not ready (see above). Not starting." -ForegroundColor Red
  return
}

# ── 3. the finance frontend copy ────────────────────────────────────────────────────────────
$feSrc = Join-Path $root "apps\frontend"
$feDst = Join-Path $fin "frontend"
$env:NEXT_PUBLIC_GATEWAY_URL  = "http://127.0.0.1:$gatewayPort"
$env:NEXT_PUBLIC_WALNUT_TOKEN = "dev-local-only"
$env:NEXT_TELEMETRY_DISABLED  = "1"
$needBuild = $RebuildFrontend -or -not (Test-Path (Join-Path $feDst ".next\BUILD_ID"))
if ($needBuild) {
  Write-Host "Preparing the finance frontend copy in $feDst ..." -ForegroundColor Cyan
  robocopy $feSrc $feDst /MIR /XD node_modules .next test-results e2e /XF tsconfig.tsbuildinfo /NFL /NDL /NJH /NJS /NP | Out-Null
  if (-not (Test-Path (Join-Path $feDst "node_modules"))) {
    cmd /c mklink /J "$feDst\node_modules" "$feSrc\node_modules" | Out-Null
  }
  # the copy's own env file (the process env above wins anyway; this keeps the copy self-describing)
  Set-Content -Path (Join-Path $feDst ".env.local") -Encoding ascii -Value @(
    "NEXT_PUBLIC_GATEWAY_URL=http://127.0.0.1:$gatewayPort", "NEXT_TELEMETRY_DISABLED=1")
  Write-Host "Building the finance frontend (npm run build, ~1 minute)..." -ForegroundColor Cyan
  Push-Location $feDst; npm run build; $buildExit = $LASTEXITCODE; Pop-Location
  if ($buildExit -ne 0) { Write-Host "  frontend build FAILED (see above)" -ForegroundColor Red; return }
}

# ── 4. gateway + frontend ───────────────────────────────────────────────────────────────────
Write-Host "Freeing ports $gatewayPort + $frontendPort (only these)..." -ForegroundColor Cyan
Free-Port $gatewayPort
Free-Port $frontendPort
Start-Sleep -Milliseconds 600

# pwsh (7+) writes the logs as UTF-8; Windows PowerShell 5's Tee-Object would write UTF-16.
$shell = if (Get-Command pwsh -ErrorAction SilentlyContinue) { "pwsh" } else { "powershell" }
$gwLog = Join-Path $fin "logs\gateway.log"
$feLog = Join-Path $fin "logs\frontend.log"
Write-Host "Starting the finance gateway (port $gatewayPort)..." -ForegroundColor Green
Start-Process $shell -ArgumentList '-NoExit', '-Command', "`$host.UI.RawUI.WindowTitle='Walnut FINANCE gateway :$gatewayPort'; cd '$root'; python scripts\finance_live\serve_finance.py --port $gatewayPort --frontend-port $frontendPort *>&1 | Tee-Object -FilePath '$gwLog'"
Write-Host "Starting the finance frontend (port $frontendPort)..." -ForegroundColor Green
Start-Process $shell -ArgumentList '-NoExit', '-Command', "`$host.UI.RawUI.WindowTitle='Walnut FINANCE frontend :$frontendPort'; cd '$feDst'; npx next start -p $frontendPort *>&1 | Tee-Object -FilePath '$feLog'"

$ok = $false
for ($i = 0; $i -lt 45; $i++) {
  try { $h = Invoke-RestMethod -Uri "http://127.0.0.1:$gatewayPort/health" -TimeoutSec 2; $ok = $true; break } catch { }
  Start-Sleep -Seconds 2
}
Write-Host ""
if ($ok) { Write-Host "Finance gateway is up." -ForegroundColor Green }
else { Write-Host "The finance gateway did not answer /health within 90 s; see $gwLog" -ForegroundColor Yellow }
Write-Host "Open:  http://localhost:$frontendPort/builder     (connect + approve + ingest the finance sources)" -ForegroundColor White
Write-Host "       http://localhost:$frontendPort/graph       (the finance graph)" -ForegroundColor White
Write-Host "       http://localhost:$frontendPort/brain       (ask questions)" -ForegroundColor White
Write-Host "Guide: ..\FINANCE_LIVE_GUIDE.md      Stop: .\stop-walnut-finance.ps1" -ForegroundColor DarkGray
