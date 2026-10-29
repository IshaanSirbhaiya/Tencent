#!/usr/bin/env bash
# One command for the Demo Day stack (Walnut DataBridge, finals build).
#
#   bash scripts/demo_up.sh start [--fresh]   # mock platforms :8200, Internal Brain :8100, seed,
#                                             # Walnut engine gateway :8300, web app :3100
#   bash scripts/demo_up.sh status
#   bash scripts/demo_up.sh stop
#
# --fresh   wipe the demo state (var/ib-final) and seed ~300 labelled replay questions again.
#
# Live Tencent Hunyuan: set IB_FORCE_OFFLINE=0 (the key comes from databridge/.env.ib.local, which is
# git-ignored). Without it the Brain answers with the honest extractive fallback.
# Everything binds to 127.0.0.1. Set IB_DEMO_PASSCODE to require a passcode at sign-in.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
STATE="${IB_VAR:-var/ib-final}"
LOGS="$STATE/logs"
WEB="apps/internal_brain_web"
PY=".venv/Scripts/python.exe"; [ -x "$PY" ] || PY=".venv/bin/python"
SEP="$("$PY" -c 'import os; print(os.pathsep)')"
IB_PATH="apps/internal_brain${SEP}packages/core${SEP}packages/harness${SEP}packages/adapter${SEP}packages/retrieval"

port_pids() {
  if command -v netstat >/dev/null 2>&1 && [ "${OS:-}" = "Windows_NT" ]; then
    netstat -ano -p tcp 2>/dev/null | awk -v p=":$1" '$2 ~ p"$" && $4 == "LISTENING" {print $5}' | sort -u
  elif command -v lsof >/dev/null 2>&1; then
    lsof -t -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | sort -u
  fi
}
stop_port() {
  for pid in $(port_pids "$1"); do
    if [ "${OS:-}" = "Windows_NT" ]; then taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
    else kill "$pid" 2>/dev/null || true; fi
  done
}
wait_http() { for _ in $(seq 1 "$2"); do curl -fsS -m 3 "$1" >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }

case "${1:-start}" in
  stop)
    for p in 3100 8100 8200; do stop_port "$p"; done
    bash scripts/run_walnut_original.sh stop || true
    echo "stopped"; exit 0 ;;
  status)
    for p in 3100 8100 8200 8300; do
      if [ -n "$(port_pids "$p")" ]; then echo "  :$p up"; else echo "  :$p down"; fi
    done; exit 0 ;;
  start) ;;
  *) echo "usage: $0 start [--fresh] | status | stop" >&2; exit 2 ;;
esac

FRESH=0; [ "${2:-}" = "--fresh" ] && FRESH=1
if [ -f .env.ib.local ]; then set -a; . ./.env.ib.local; set +a; fi
export IB_FORCE_OFFLINE="${IB_FORCE_OFFLINE:-1}" IB_DEMO_LOGIN=1 IB_POLL_S="${IB_POLL_S:-120}" IB_VAR="$STATE"
export IB_REPLAY_KEY="${IB_REPLAY_KEY:-$("$PY" -c 'import secrets; print(secrets.token_hex(16))')}"

if [ "$FRESH" = "1" ]; then
  for p in 8100 8200; do stop_port "$p"; done
  case "$STATE" in var/*) rm -rf "$STATE" ;; *) echo "refusing to wipe $STATE (not under var/)" >&2; exit 2 ;; esac
fi
mkdir -p "$LOGS"
SEED=0; [ ! -d "$STATE/ledger" ] && SEED=1

echo "[1/5] mock platforms :8200"
if [ -z "$(port_pids 8200)" ]; then
  ( exec </dev/null >"$LOGS/mock.log" 2>&1
    PYTHONPATH="apps/mock_platforms" MOCK_WEBHOOK_URL="http://127.0.0.1:8100" nohup "$PY" -m mock_platforms --port 8200 & )
fi
wait_http http://127.0.0.1:8200/health 60 || { echo "mock platforms did not start; see $LOGS/mock.log" >&2; exit 1; }

echo "[2/5] Internal Brain :8100 (IB_FORCE_OFFLINE=$IB_FORCE_OFFLINE)"
if [ -z "$(port_pids 8100)" ]; then
  ( exec </dev/null >"$LOGS/ib.log" 2>&1
    PYTHONPATH="$IB_PATH" nohup "$PY" -m databridge_ib & )
fi
wait_http http://127.0.0.1:8100/health 180 || { echo "Internal Brain did not start; see $LOGS/ib.log" >&2; exit 1; }

if [ "$SEED" = "1" ]; then
  echo "[3/5] seeding labelled demo history (about 2 minutes)"
  "$PY" scripts/seed_demo_activity.py --base http://127.0.0.1:8100 >"$LOGS/seed.log" 2>&1 \
    || echo "  seeding reported a problem; see $LOGS/seed.log (the demo still works)"
  tail -n 3 "$LOGS/seed.log" || true
else
  echo "[3/5] demo history present (use --fresh to reseed)"
fi

echo "[4/5] Walnut engine gateway :8300"
bash scripts/run_walnut_original.sh start >"$LOGS/engine.log" 2>&1 || echo "  engine did not start; see $LOGS/engine.log (only /dev/brain needs it)"

echo "[5/5] web app :3100"
if [ -z "$(port_pids 3100)" ]; then
  ( cd "$WEB" && NEXT_TELEMETRY_DISABLED=1 WALNUT_PROXY=1 IB_API_URL=http://127.0.0.1:8100 npx next build >"$ROOT/$LOGS/web-build.log" 2>&1 ) \
    || { echo "web build failed; see $LOGS/web-build.log" >&2; exit 1; }
  ( exec </dev/null >"$LOGS/web.log" 2>&1
    cd "$WEB" && WALNUT_PROXY=1 IB_API_URL=http://127.0.0.1:8100 nohup npx next start -H 127.0.0.1 -p 3100 & )
fi
wait_http http://127.0.0.1:3100/login 120 || { echo "web app did not start; see $LOGS/web.log" >&2; exit 1; }

cat <<EOF

Walnut DataBridge is up.
  App               http://127.0.0.1:3100            (User mode; switch to Developer top-left)
  Presenter         http://127.0.0.1:3100/compare?present=1
  Developer         http://127.0.0.1:3100/dev
  Live AI           IB_FORCE_OFFLINE=$IB_FORCE_OFFLINE (0 = Tencent Hunyuan Hy3 via TokenHub)
  Logs              $LOGS/
Stop with: bash scripts/demo_up.sh stop
EOF
