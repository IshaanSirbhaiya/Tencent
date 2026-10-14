#!/usr/bin/env bash
# Runs the CodeBuddy build sessions CB1-CB5 headless, one at a time, from the repo root.
# Each session's full stream-json transcript is kept as proof of CodeBuddy usage.
#   bash docs/tencent/evidence/run_codebuddy.sh cb1 cb2 ...
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
EV="$ROOT/docs/tencent/evidence"
cd "$ROOT"
for s in "$@"; do
  case "$s" in
    cb1|cb5) model="hy3" ;;
    *) model="default-model" ;;
  esac
  prompt="$(cat "$EV/codebuddy_prompts/_rules.md" "$EV/codebuddy_prompts/$s.md")"
  echo "[$(date '+%H:%M:%S')] start $s (model $model)"
  codebuddy -p "$prompt" --model "$model" -y --disallowedTools "Bash(git:*)" \
    --output-format stream-json > "$EV/codebuddy_$s.jsonl" 2> "$EV/codebuddy_$s.stderr.log"
  echo "[$(date '+%H:%M:%S')] end $s rc=$?"
done
