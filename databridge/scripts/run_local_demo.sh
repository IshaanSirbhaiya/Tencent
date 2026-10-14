#!/usr/bin/env bash
# Start the local demo stack: mock platforms (:8200) + Internal Brain (:8100).
# Secrets come from databridge/.env.ib.local when it exists (git-ignored); otherwise the
# service runs on its built-in dev keys. The web app (:3100) is started separately:
#   cd apps/internal_brain_web && npm run dev
#
#   bash scripts/run_local_demo.sh [state-dir]      # Git Bash / macOS / Linux
#
# IB_FORCE_OFFLINE=1 (the default here) keeps answers deterministic; set IB_FORCE_OFFLINE=0 to let
# the Brain call Tencent TokenHub (hy3 -> deepseek-v4.1-flash) when TOKENHUB_API_KEY is set.
set -euo pipefail
cd "$(dirname "$0")/.."
STATE="${1:-var/ib-demo}"
PY=".venv/Scripts/python.exe"; [ -x "$PY" ] || PY=".venv/bin/python"
if [ -f .env.ib.local ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env.ib.local
  set +a
fi
export IB_FORCE_OFFLINE="${IB_FORCE_OFFLINE:-1}" IB_DEMO_LOGIN=1 IB_POLL_S="${IB_POLL_S:-120}" IB_VAR="$STATE"
mkdir -p "$STATE"
PYTHONPATH="apps/mock_platforms" MOCK_WEBHOOK_URL="http://127.0.0.1:8100" \
  "$PY" -m mock_platforms --port 8200 > "$STATE/mock.log" 2>&1 &
for _ in $(seq 1 30); do curl -s -m 2 http://127.0.0.1:8200/health >/dev/null && break; sleep 1; done
echo "mock platforms on :8200 (log $STATE/mock.log); Internal Brain on :8100 (Ctrl+C to stop)"
SEP="$("$PY" -c 'import os; print(os.pathsep)')"   # ";" on Windows, ":" elsewhere
PYTHONPATH="apps/internal_brain${SEP}packages/core${SEP}packages/harness${SEP}packages/adapter${SEP}packages/retrieval" \
  exec "$PY" -m databridge_ib
