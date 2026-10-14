#!/usr/bin/env bash
# Run the ORIGINAL Walnut frontend (apps/frontend) on Company A, the Internal Brain's synthetic world.
#
#   bash scripts/run_walnut_original.sh          # build what is missing, start both, print URLs
#   bash scripts/run_walnut_original.sh status   # what is listening on the two ports
#   bash scripts/run_walnut_original.sh stop     # stop ONLY the processes on 8300 and 3300
#   REBUILD_GRAPH=1 bash scripts/run_walnut_original.sh   # rebuild var/graph/enterprise.db first
#   REBUILD_WEB=1   bash scripts/run_walnut_original.sh   # force a fresh `next build`
#
# Ports: gateway 127.0.0.1:8300, web http://localhost:3300. Ports 8100, 8200 and 3100 belong to
# the Internal Brain and are never touched by this script. It does not free a busy port: if 8300
# or 3300 is already taken it says so and stops.
#
# LOCAL ONLY. The web build carries the dev token `dev-local-only` (inlined by Next at build time)
# and the gateway accepts it only because WALNUT_DEV_AUTH=1 is set here. Never expose these ports.
#
# Works from Git Bash on Windows and from bash on macOS/Linux. Paths are passed as single values
# (no PATH-style lists), so the ; vs : separator never matters.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"                       # databridge/
WEB="$ROOT/apps/frontend"
GW_PORT="${WALNUT_ORIGINAL_GATEWAY_PORT:-8300}"
WEB_PORT="${WALNUT_ORIGINAL_WEB_PORT:-3300}"
GW_URL="http://127.0.0.1:$GW_PORT"
TOKEN="dev-local-only"
GRAPH_DB="var/graph/enterprise.db"
STATE="$ROOT/var/walnut_original"
LOGS="$STATE/logs"

for reserved in 8100 8200 3100; do
  if [ "$GW_PORT" = "$reserved" ] || [ "$WEB_PORT" = "$reserved" ]; then
    echo "refusing: port $reserved belongs to the Internal Brain" >&2; exit 2
  fi
done

if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then PY="$ROOT/.venv/Scripts/python.exe"
elif [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"
else echo "no virtualenv at $ROOT/.venv (see the repo CLAUDE.md 'Running it')" >&2; exit 2
fi

# --- port helpers (read-only unless `stop`) ------------------------------------------------------
port_pids() {   # PIDs listening on TCP port $1, one per line
  if command -v netstat >/dev/null 2>&1 && [ "${OS:-}" = "Windows_NT" ]; then
    netstat -ano -p tcp 2>/dev/null | awk -v p=":$1" '$2 ~ p"$" && $4 == "LISTENING" {print $5}' | sort -u
  elif command -v lsof >/dev/null 2>&1; then
    lsof -t -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | sort -u
  fi
}
port_busy() { [ -n "$(port_pids "$1")" ]; }

wait_http() {   # wait_http URL SECONDS [HEADER]
  local url="$1" secs="$2" hdr="${3:-}"
  for _ in $(seq 1 "$secs"); do
    if [ -n "$hdr" ]; then curl -fsS -H "$hdr" "$url" >/dev/null 2>&1 && return 0
    else curl -fsS "$url" >/dev/null 2>&1 && return 0; fi
    sleep 1
  done
  return 1
}

stop_port() {
  local p="$1" pid
  for pid in $(port_pids "$p"); do
    echo "  stopping PID $pid on port $p"
    if [ "${OS:-}" = "Windows_NT" ]; then taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
    else kill "$pid" 2>/dev/null || true; fi
  done
}

case "${1:-start}" in
  status)
    echo "gateway $GW_PORT: $(port_pids "$GW_PORT" | tr '\n' ' ')"
    echo "web     $WEB_PORT: $(port_pids "$WEB_PORT" | tr '\n' ' ')"
    exit 0 ;;
  stop)
    stop_port "$GW_PORT"; stop_port "$WEB_PORT"; exit 0 ;;
  start) ;;
  *) echo "usage: $0 [start|status|stop]" >&2; exit 2 ;;
esac

mkdir -p "$LOGS" "$ROOT/var/graph"

# --- 1. the graph --------------------------------------------------------------------------------
if [ "${REBUILD_GRAPH:-0}" = "1" ] || [ ! -s "$ROOT/$GRAPH_DB" ]; then
  echo "[1/3] building $GRAPH_DB from fixtures/enterprise/world.json ..."
  (cd "$ROOT" && "$PY" scripts/build_enterprise_graph.py --out "$GRAPH_DB")
else
  echo "[1/3] graph present: $GRAPH_DB  (REBUILD_GRAPH=1 to rebuild)"
fi

# --- 2. the gateway ------------------------------------------------------------------------------
if port_busy "$GW_PORT"; then
  echo "[2/3] port $GW_PORT is already in use; not starting a second gateway (stop it with: $0 stop)" >&2
else
  echo "[2/3] starting the gateway on $GW_URL (log: $LOGS/gateway.log)"
  (
    # Detach from this shell's stdout/stderr first: on Windows a child inherits every open handle,
    # and a server holding the caller's pipe keeps `... | tail` waiting forever.
    exec </dev/null >/dev/null 2>&1
    cd "$ROOT"
    export DATABRIDGE_PACKS="enterprise"
    export WALNUT_GRAPH_DB_PATH="$GRAPH_DB"
    export WALNUT_GATEWAY_PORT="$GW_PORT"
    export WALNUT_DEV_AUTH="1"
    export WALNUT_CORS_ORIGINS="http://localhost:$WEB_PORT,http://127.0.0.1:$WEB_PORT"
    # Keep this deployment's working files apart from any other Walnut run on this machine.
    export WALNUT_CAPTURE_DIR="var/walnut_original/capture"
    export WALNUT_SOURCES_DIR="var/walnut_original/sources"
    export WALNUT_INGEST_DIR="var/walnut_original/ingest"
    export WALNUT_RUNS_DIR="var/walnut_original/runs"
    export WALNUT_EVENTS_PATH="var/walnut_original/events.jsonl"
    export WALNUT_ONTOLOGY_AUDIT_PATH="var/walnut_original/ontology_audit.jsonl"
    export PYTHONUNBUFFERED=1
    nohup "$PY" apps/gateway/serve.py >"$LOGS/gateway.log" 2>&1 &
  )
fi
if ! wait_http "$GW_URL/health" 90; then
  echo "gateway did not answer $GW_URL/health in 90s; see $LOGS/gateway.log" >&2; exit 1
fi
echo "      gateway healthy"

# --- 3. the web app ------------------------------------------------------------------------------
STAMP_WANT="gateway=$GW_URL token=$TOKEN"
STAMP_FILE="$WEB/.next/WALNUT_ORIGINAL_BUILD"
if [ ! -d "$WEB/node_modules" ]; then
  echo "[3/3] installing web dependencies (npm ci) ..."
  (cd "$WEB" && npm ci --no-audit --no-fund)
fi
if [ "${REBUILD_WEB:-0}" = "1" ] || [ ! -f "$WEB/.next/BUILD_ID" ] || \
   [ "$(cat "$STAMP_FILE" 2>/dev/null || true)" != "$STAMP_WANT" ]; then
  echo "[3/3] building the web app for $GW_URL (log: $LOGS/web-build.log) ..."
  (cd "$WEB" && NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_GATEWAY_URL="$GW_URL" \
     NEXT_PUBLIC_WALNUT_TOKEN="$TOKEN" npx next build >"$LOGS/web-build.log" 2>&1) || {
     echo "next build failed; see $LOGS/web-build.log" >&2; exit 1; }
  printf '%s' "$STAMP_WANT" >"$STAMP_FILE"
else
  echo "[3/3] web build present for $GW_URL  (REBUILD_WEB=1 to rebuild)"
fi
if port_busy "$WEB_PORT"; then
  echo "      port $WEB_PORT is already in use; not starting a second web server" >&2
else
  echo "      starting the web app on http://localhost:$WEB_PORT (log: $LOGS/web.log)"
  (exec </dev/null >/dev/null 2>&1
   cd "$WEB" && NEXT_TELEMETRY_DISABLED=1 nohup npx next start -H 127.0.0.1 -p "$WEB_PORT" >"$LOGS/web.log" 2>&1 &)
fi
if ! wait_http "http://localhost:$WEB_PORT/" 90; then
  echo "web app did not answer on :$WEB_PORT in 90s; see $LOGS/web.log" >&2; exit 1
fi

cat <<EOF

Walnut (original screens) on Company A is running. Local only.
  Knowledge graph   http://localhost:$WEB_PORT/graph
  Company brain     http://localhost:$WEB_PORT/brain
  Ontology          http://localhost:$WEB_PORT/ontology
  Provenance        http://localhost:$WEB_PORT/evidence?entity=knowledge_page%3Acf-1001
  Guided tour       http://localhost:$WEB_PORT/demo
  Gateway health    $GW_URL/health
Sign in as Jordan Doe (the graph is his view). Stop with: bash scripts/run_walnut_original.sh stop
EOF
