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

# The frontend must send the gateway's local dev token (see the R-015 note below and
# apps/frontend/lib/api.ts `authHeaders`). NEXT_PUBLIC_* is inlined at BUILD time, so this is set
# before the build; a .next built without it cannot see /graph, /trace, /evidence or /intelligence.
$env:NEXT_PUBLIC_WALNUT_TOKEN = "dev-local-only"

# build the frontend if it has never been built (first run)
if (-not (Test-Path "$root\apps\frontend\.next")) {
  Write-Host "First run: building the frontend (npm run build)..." -ForegroundColor Cyan
  Push-Location "$root\apps\frontend"; npm run build; Pop-Location
}

# R-015: the gateway's disclosing routes (/orchestrate, /evidence/verify/*, /graph, /trace,
# /intelligence) now require an authenticated caller (databridge_gateway/auth.py). This is the
# ONE place in this repo that turns that on for a local demo — WALNUT_DEV_AUTH defaults to OFF
# everywhere else, on purpose, so a deployment can only run open if someone explicitly does what
# this line does. It is inherited by the gateway's child process below, not by anything else.
Write-Host "Enabling the local-demo auth token (WALNUT_DEV_AUTH=1) for the gateway process..." -ForegroundColor Yellow
$env:WALNUT_DEV_AUTH = "1"

# R-022 round 2 (FXJ3, Codex f2 #10): source registration is FAIL-CLOSED by default — the
# command allow-list / SSRF policy applies unless the DEPLOYMENT declares operator mode. This
# local launcher is the host operator, so it declares it here, for the gateway process only.
# The gateway still passes its own remote-caller policy before it registers anything.
$env:WALNUT_SOURCE_REGISTRATION = "operator"

# R-022 round 2 (FXJ3, Codex f2 #3): infra/mcp_servers.yaml no longer carries default database
# passwords (`${VAR:-default}` in a credential position is refused). Before importing the demo
# Postgres sources, set these in YOUR shell (synthetic demo values; see
# infra/docker-compose.walnut.yml and profiles/postgres/agent_writer.sql for the roles):
#   $env:WALNUT_DEMO_PG_READER_PASSWORD = "<the walnut_reader password>"
#   $env:WALNUT_DEMO_PG_WRITER_PASSWORD = "<the walnut_agent_writer password>"
#   $env:WALNUT_DEMO_PG_PORT            = "5433"   # optional; this one may default
# A local demo that insists on defaulted credentials can set
# $env:WALNUT_DEV_ALLOW_CREDENTIAL_DEFAULTS = "1" (dev only; never in a deployment).

# R-029 TIDY2: the deployment approval key (it seals every human decision) is NEVER created
# implicitly any more: a read never writes, and a decision with no key is refused. This launcher is
# the host operator, so it provisions the key ONCE, explicitly (WALNUT_APPROVAL_KEY_FILE, default
# ~/.walnut/keys/approval.key). An existing key is never overwritten.
python -c "from databridge_core.decision_ledger import provision_approval_key as p; p()"

Write-Host "Starting gateway (backend, port 8000)..." -ForegroundColor Green
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root'; python apps\gateway\serve.py"

Write-Host "Starting frontend (port 3000)..." -ForegroundColor Green
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root\apps\frontend'; npm run start"

Write-Host ""
Write-Host "DataBridge is starting in two new windows. Give it ~10 seconds, then open:" -ForegroundColor Cyan
Write-Host "    http://localhost:3000/brain" -ForegroundColor White
Write-Host "Gateway health:  http://127.0.0.1:8000/health  (should say llm_backend: openai)" -ForegroundColor DarkGray
