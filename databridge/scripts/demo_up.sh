#!/usr/bin/env bash
# One command for the Demo Day stack (Walnut DataBridge, finals build).
#
#   bash scripts/demo_up.sh start [--fresh]   # mock platforms :8200, Internal Brain :8100, seed,
#                                             # Walnut engine gateway :8300, web app :3100
#   bash scripts/demo_up.sh status
#   bash scripts/demo_up.sh stop
#
# --fresh   wipe the demo state (var/ib-final) and seed ~300 labelled replay questions again.
#           The model spend ledger (var/model_guard) is outside it and is never wiped (D-42).
#
# The model: scripts/hy3_check.py decides IB_TOKENHUB_LIVE (1 only when TokenHub really answers; the
# key comes from databridge/.env.ib.local, git-ignored, and may stay there when it is not live). Not
# live: the opt-in stand-in (IB_CLAUDE=1) if on, else the honest extractive fallback.
# Everything binds to 127.0.0.1. Set IB_DEMO_PASSCODE to require a passcode at sign-in.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
STATE="${IB_VAR:-var/ib-final}"
# the stand-in model's spend ledger (var/model_guard) is never the state and never inside it (D-42)
. scripts/wipe_guard.sh
LOGS="$STATE/logs"
WEB="apps/internal_brain_web"
# Absolute interpreter path: the command line of every Python service we start then names this
# repo, which is how `stop` tells our processes from someone else's on the same port.
PY="$ROOT/.venv/Scripts/python.exe"; [ -x "$PY" ] || PY="$ROOT/.venv/bin/python"
SEP="$("$PY" -c 'import os; print(os.pathsep)')"
IB_PATH="apps/internal_brain${SEP}packages/core${SEP}packages/harness${SEP}packages/adapter${SEP}packages/retrieval"

# PID 0 is Windows' placeholder for a socket whose owner just exited; it is never a live listener.
port_pids() {
  if command -v netstat >/dev/null 2>&1 && [ "${OS:-}" = "Windows_NT" ]; then
    netstat -ano -p tcp 2>/dev/null | awk -v p=":$1" '$2 ~ p"$" && $4 == "LISTENING" && $5 != "0" {print $5}' | sort -u
  elif command -v lsof >/dev/null 2>&1; then
    lsof -t -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | sort -u
  fi
}
# The forms this repo's path can take in a command line (lower case, forward slashes).
repo_marks() {
  printf '%s\n' "$ROOT" | tr 'A-Z\\' 'a-z/'
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$ROOT" | tr 'A-Z\\' 'a-z/'
    cygpath -m "$ROOT" | tr 'A-Z\\' 'a-z/'
  fi
}
# A process's command line and (where the OS shows it) its working directory.
proc_info() {
  if [ "${OS:-}" = "Windows_NT" ]; then
    powershell.exe -NoProfile -NonInteractive -Command \
      "(Get-CimInstance Win32_Process -Filter 'ProcessId=$1').CommandLine" 2>/dev/null || true
  elif [ -r "/proc/$1/cmdline" ]; then
    tr '\0' ' ' <"/proc/$1/cmdline" 2>/dev/null || true; echo
    readlink "/proc/$1/cwd" 2>/dev/null || true
  else
    ps -o args= -p "$1" 2>/dev/null || true
    lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' || true
  fi
}
# True only when the process's command line (or working directory) names this repo.
owned() {
  local info m
  info="$(proc_info "$1" | tr 'A-Z\\' 'a-z/' | sed 's|$|/|')"   # "/repo" must not match "/repo-other"
  [ -n "$info" ] || return 1
  while IFS= read -r m; do
    [ -n "$m" ] || continue
    case "$info" in *"$m/"*) return 0 ;; esac
  done <<MARKS
$(repo_marks)
MARKS
  return 1
}
# Stop only this repo's processes on a port; anything else there is reported, never killed.
stop_port() {
  for pid in $(port_pids "$1"); do
    if ! owned "$pid"; then
      echo "  port $1: process $pid is not part of this repo ($ROOT); not stopping it" >&2
      continue
    fi
    if [ "${OS:-}" = "Windows_NT" ]; then taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
    else kill "$pid" 2>/dev/null || true; fi
  done
}
wait_http() { for _ in $(seq 1 "$2"); do curl -fsS -m 3 "$1" >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }
# Start a service only when nothing answers on its health URL AND nothing listens on its port, so a
# second start never launches a duplicate (a duplicate Internal Brain races the live one for the
# ledger). netstat can miss a listener (e.g. one started from another console); the probe cannot.
need_start() { ! curl -fsS -m 3 "$2" >/dev/null 2>&1 && [ -z "$(port_pids "$1")" ]; }
# The values the other processes must agree on with the Brain.
SHARED_KEYS="IB_FORCE_OFFLINE IB_POLL_S IB_ENGINE_URL IB_ENGINE_TOKEN IB_DELIVERABLES IB_REPLAY_KEY \
IB_DEMO_PASSCODE MOCK_SVC_TOKEN MOCK_ADMIN_TOKEN MOCK_WEBHOOK_SECRET"
# Round-4 Opus #8: the whole file is sourced for a child only inside the Internal Brain's own subshell
# (step 2), so the TokenHub key and the IB's signing keys in it never reach the mock platforms, the
# seeder, the engine or Next. Round-5 Opus #2: the shared values are read by bash itself (the Brain
# sources the same file with bash), in a throw-away subshell that prints only SHARED_KEYS as
# NUL-separated KEY, value pairs. So a '#', quotes or an escape mean the same thing to every process;
# a second parser (shlex) could disagree with bash. Nothing else from the file leaves the subshell.
envfile_read() {
  ( set +eu
    for k in $SHARED_KEYS; do unset "$k"; done
    set -a; . ./.env.ib.local </dev/null >/dev/null 2>&1; set +a
    for k in $SHARED_KEYS; do printf '%s\0%s\0' "$k" "${!k-}"; done )
}
# Keys only the Internal Brain may hold: the model keys and the opt-in stand-in model's switches. They
# reach the Brain from the parent shell or from .env.ib.local (sourced only in the Brain's subshell),
# and are unset in every other child (mock platforms, seeder, engine, Next), as TOKENHUB_API_KEY is.
IB_ONLY_KEYS="TOKENHUB_API_KEY ANTHROPIC_API_KEY IB_CLAUDE IB_CLAUDE_BUDGET_USD IB_CLAUDE_FAST \
IB_CLAUDE_EFFORT IB_CLAUDE_ANSWER_EFFORT IB_CLAUDE_USER_LIMIT IB_MODEL_CAP_USD IB_MODEL_DAILY_CAP_USD \
IB_MODEL_RPM IB_MODEL_USER_RPM IB_MODEL_GUARD_DIR"
# For the banner only: is the opt-in stand-in model on for the Brain? Decided in a throw-away subshell
# that sources the file the way the Brain does; it prints "on" or "off", never a key or a value.
standin_state() {
  ( set +eu
    if [ -f .env.ib.local ]; then set -a; . ./.env.ib.local </dev/null >/dev/null 2>&1; set +a; fi
    if [ "${IB_CLAUDE:-}" = "1" ] && [ -n "${ANTHROPIC_API_KEY:-}" ] && [ "${_S_IB_FORCE_OFFLINE:-}" != "1" ]
    then echo on; else echo off; fi )
}
# Should the stand-in answer when TokenHub does not? Only when it is switched on (IB_CLAUDE=1 and its
# key). A TokenHub key may stay in .env.ib.local: the Brain builds the TokenHub chain only when
# IB_TOKENHUB_LIVE=1 (set below from the real hy3_check result), so a key that answers 402 is never
# tried per ask and never hides the stand-in.
# Same throw-away subshell as above; prints "on" or "off", never a key or a value.
standin_wanted() {
  ( set +eu
    if [ -f .env.ib.local ]; then set -a; . ./.env.ib.local </dev/null >/dev/null 2>&1; set +a; fi
    if [ "${IB_CLAUDE:-}" = "1" ] && [ -n "${ANTHROPIC_API_KEY:-}" ]
    then echo on; else echo off; fi )
}
# The values this script settles on (forced switches, then the file, then defaults), copied into
# private, unexported names _S_<KEY>, so the Brain's subshell can put them back after it sources the
# file. A key that is unset stays unset (a child's own default then applies), never "".
SETTLED_KEYS="IB_FORCE_OFFLINE IB_TOKENHUB_LIVE IB_DEMO_LOGIN IB_POLL_S IB_VAR IB_DEMO_FEATURES IB_ENGINE_URL IB_ENGINE_TOKEN \
IB_DELIVERABLES IB_REPLAY_KEY IB_DEMO_PASSCODE MOCK_SVC_TOKEN MOCK_ADMIN_TOKEN MOCK_WEBHOOK_SECRET"
save_settled() {
  local k
  for k in $SETTLED_KEYS; do
    if [ -n "${!k+x}" ]; then printf -v "_S_$k" '%s' "${!k}"; else unset "_S_$k"; fi
  done
}
# Export the settled value of each named key (or unset it) in the current (sub)shell.
export_settled() {
  local k s
  for k in "$@"; do
    s="_S_$k"
    if [ -n "${!s+x}" ]; then export "$k=${!s}"; else unset "$k"; fi
  done
}

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
if [ -f .env.ib.local ]; then
  # Only the values the other processes must agree on with the Brain (as before, a non-empty value in
  # the file wins). The IB_* ones are exported below and reach every child; the MOCK_* tokens and the
  # passcode go only to the mock platforms, the seeder and the Brain (below), as the same strings.
  while IFS= read -r -d '' k && IFS= read -r -d '' v; do
    case " $SHARED_KEYS " in *" $k "*) ;; *) continue ;; esac
    if [ -n "$v" ]; then printf -v "$k" '%s' "$v"; fi
  done < <(envfile_read)
  unset k v
elif [ "${ALLOW_DEV_KEYS:-0}" != "1" ]; then
  # Without the local keys the Brain would start on its dev-default keys and could not append to the
  # demo ledger (sign-in 503, Verify fails). Usually the CodeBuddy runner has the file moved aside.
  echo "refusing to start: databridge/.env.ib.local is missing (is a CodeBuddy session running?)." >&2
  echo "Wait for it to finish, or set ALLOW_DEV_KEYS=1 for a throwaway state dir." >&2
  exit 3
fi
# IB_TOKENHUB_LIVE is ALWAYS the real hy3_check result (an inherited value is ignored): the Brain
# builds the TokenHub chain only when it is 1, and Hy3/Hunyuan is called LIVE only then.
IB_TOKENHUB_LIVE=0
if [ "${IB_FORCE_OFFLINE:-}" != "1" ] && "$PY" scripts/hy3_check.py --quiet; then IB_TOKENHUB_LIVE=1; fi
if [ "$IB_TOKENHUB_LIVE" = "1" ]; then
  # Live Tencent Hunyuan: TokenHub answers for this account
  IB_FORCE_OFFLINE="${IB_FORCE_OFFLINE:-0}"
elif [ "$(standin_wanted)" = on ] && [ "${IB_FORCE_OFFLINE:-}" != "1" ]; then
  # TokenHub not live (a key may still be set) and the opt-in stand-in is on: stay online for it
  IB_FORCE_OFFLINE=0
elif [ -z "${IB_FORCE_OFFLINE:-}" ]; then
  IB_FORCE_OFFLINE=1      # the honest fallback: quoted answers
fi
export IB_FORCE_OFFLINE IB_TOKENHUB_LIVE IB_DEMO_LOGIN=1 IB_POLL_S="${IB_POLL_S:-120}" IB_VAR="$STATE"
# Demo-proof features (D-24): live growth, "Add a document", the absence check and the engine
# refresh. Set ONLY here, for this local demo stack (off by default everywhere else; never on a
# deployment). Viewers still turn them on in Settings. The engine refresh asks the local gateway
# (:8300, WALNUT_DEV_AUTH=1) to reload with its fixed local dev token, server-side only.
export IB_DEMO_FEATURES=1 IB_ENGINE_URL="${IB_ENGINE_URL:-http://127.0.0.1:8300}" IB_ENGINE_TOKEN="${IB_ENGINE_TOKEN:-dev-local-only}"
# Deliverables from an answer (D-25): a KILL SWITCH, not a demo-only switch. On here; to turn
# it off, unset it and restart the IB (it is read at start-up).
export IB_DELIVERABLES="${IB_DELIVERABLES:-1}"
export IB_REPLAY_KEY="${IB_REPLAY_KEY:-$("$PY" -c 'import secrets; print(secrets.token_hex(16))')}"
save_settled   # from here on, _S_<KEY> holds what every child must see

# never run on (or wipe) a state that is, holds or sits in the model spend ledger dir (D-42)
refuse_guard_overlap "$STATE" || exit 2
if [ "$FRESH" = "1" ]; then
  for p in 8100 8200; do stop_port "$p"; done
  # Round-4 Opus #11: stop_port leaves alone anything it cannot prove is ours. If a listener is still
  # there, the old Brain may hold the ledger open: never wipe under it.
  held=""
  for _ in 1 2 3 4 5; do                       # a stopped listener can take a moment to let go
    held=""
    for p in 8100 8200; do [ -z "$(port_pids "$p")" ] || held="$held :$p"; done
    [ -n "$held" ] || break
    sleep 1
  done
  if [ -n "$held" ]; then
    echo "refusing to wipe $STATE: a process still holds$held (see above). Stop it first, then run --fresh again." >&2
    exit 4
  fi
  # Only a plain path under var/: no "..", no absolute path, and it must still resolve inside var/.
  case "$STATE" in
    *..*|/*|*\\*) echo "refusing to wipe $STATE (not a plain path under var/)" >&2; exit 2 ;;
    var/?*) ;;
    *) echo "refusing to wipe $STATE (not under var/)" >&2; exit 2 ;;
  esac
  if [ -e "$STATE" ]; then
    REAL_STATE="$(cd "$STATE" && pwd -P)"; REAL_VAR="$(cd var && pwd -P)"
    case "$REAL_STATE" in "$REAL_VAR"/?*) rm -rf "$STATE" ;;
      *) echo "refusing to wipe $STATE (resolves outside var/)" >&2; exit 2 ;; esac
  fi
fi
mkdir -p "$LOGS"
SEED=0; [ ! -d "$STATE/ledger" ] && SEED=1

echo "[1/5] mock platforms :8200"
if need_start 8200 http://127.0.0.1:8200/health; then
  ( exec </dev/null >"$LOGS/mock.log" 2>&1
    unset $IB_ONLY_KEYS                        # SECURITY 15.15: even when the parent shell has them
    export_settled MOCK_SVC_TOKEN MOCK_ADMIN_TOKEN MOCK_WEBHOOK_SECRET
    PYTHONPATH="apps/mock_platforms" MOCK_WEBHOOK_URL="http://127.0.0.1:8100" nohup "$PY" -m mock_platforms --port 8200 & )
fi
wait_http http://127.0.0.1:8200/health 60 || { echo "mock platforms did not start; see $LOGS/mock.log" >&2; exit 1; }

echo "[2/5] Internal Brain :8100 (IB_FORCE_OFFLINE=$IB_FORCE_OFFLINE IB_TOKENHUB_LIVE=$IB_TOKENHUB_LIVE)"
if need_start 8100 http://127.0.0.1:8100/health; then
  ( exec </dev/null >"$LOGS/ib.log" 2>&1
    # The whole local key file, for the Brain only; then the values this script settled on win again
    # (forced demo switches, then file, then defaults). They come from the private _S_* copies, not
    # from the names the source just overwrote (round-5 Opus #2: an empty IB_POLL_S= in the file must
    # not reach the Brain as ""), so the mocks, the seeder and the Brain hold the same strings.
    if [ -f .env.ib.local ]; then set -a; . ./.env.ib.local; set +a; fi
    export_settled $SETTLED_KEYS
    PYTHONPATH="$IB_PATH" nohup "$PY" -m databridge_ib & )
fi
wait_http http://127.0.0.1:8100/health 180 || { echo "Internal Brain did not start; see $LOGS/ib.log" >&2; exit 1; }

if [ "$SEED" = "1" ]; then
  echo "[3/5] seeding labelled demo history (about 2 minutes)"
  ( unset $IB_ONLY_KEYS
    export_settled IB_DEMO_PASSCODE MOCK_ADMIN_TOKEN
    "$PY" scripts/seed_demo_activity.py --base http://127.0.0.1:8100 >"$LOGS/seed.log" 2>&1 ) \
    || echo "  seeding reported a problem; see $LOGS/seed.log (the demo still works)"
  tail -n 3 "$LOGS/seed.log" || true
else
  echo "[3/5] demo history present (use --fresh to reseed)"
fi

echo "[4/5] Walnut engine gateway :8300"
( unset $IB_ONLY_KEYS; exec bash scripts/run_walnut_original.sh start ) >"$LOGS/engine.log" 2>&1 || echo "  engine did not start; see $LOGS/engine.log (only /dev/brain needs it)"

echo "[5/5] web app :3100"
if need_start 3100 http://127.0.0.1:3100/login; then
  ( unset $IB_ONLY_KEYS; cd "$WEB" && NEXT_TELEMETRY_DISABLED=1 WALNUT_PROXY=1 IB_API_URL=http://127.0.0.1:8100 npx next build >"$ROOT/$LOGS/web-build.log" 2>&1 ) \
    || { echo "web build failed; see $LOGS/web-build.log" >&2; exit 1; }
  ( exec </dev/null >"$LOGS/web.log" 2>&1
    unset $IB_ONLY_KEYS
    cd "$WEB" && WALNUT_PROXY=1 IB_API_URL=http://127.0.0.1:8100 nohup npx next start -H 127.0.0.1 -p 3100 & )
fi
wait_http http://127.0.0.1:3100/login 120 || { echo "web app did not start; see $LOGS/web.log" >&2; exit 1; }

cat <<EOF

Walnut DataBridge is up.
  App               http://127.0.0.1:3100            (User mode; switch to Developer top-left)
  Presenter         http://127.0.0.1:3100/compare?present=1
  Developer         http://127.0.0.1:3100/dev
  Model             $( if [ "$IB_TOKENHUB_LIVE" = "1" ]; then echo "TokenHub answered the check (IB_TOKENHUB_LIVE=1); the preflight confirms what /ib/status shows"; elif [ "$IB_FORCE_OFFLINE" != "1" ] && [ "$(standin_state)" = on ]; then echo "AI model active (the stand-in; TokenHub not live, IB_TOKENHUB_LIVE=0)"; else echo "Quoted from sources (no model; IB_TOKENHUB_LIVE=0, IB_FORCE_OFFLINE=$IB_FORCE_OFFLINE)"; fi )
  AI stand-in       $( [ "$(standin_state)" = on ] && echo "on (opt-in; spend-capped; check /ib/status)" || echo "off (opt-in only; pages are laid out by rules)")
  Logs              $LOGS/
Stop with: bash scripts/demo_up.sh stop
EOF
