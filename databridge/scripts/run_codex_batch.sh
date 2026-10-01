#!/usr/bin/env bash
# Run a batch of codex reviews IN PARALLEL, verify every output, and re-run the failures until
# the whole batch is genuine — or until the retry budget is spent.
#
# Why this exists: codex fails silently. A failed run prints its banner, exits 0, and leaves a
# file that reads exactly like "reviewed, found nothing". On 2026-08-16 three parallel stdin-fed
# runs all failed that way while three sequential ones succeeded. Parallel is worth attempting
# (it is much faster when it works) but is only safe behind verification.
#
# Retry policy: attempt 1 is fully parallel. Every retry runs the survivors SEQUENTIALLY, because
# that is the mode observed to work. So the batch gets parallel's speed when the service allows
# it and sequential's reliability when it does not.
#
# Usage:  run_codex_batch.sh <manifest>
#   manifest lines:  <bundle-file>\t<output-file>\t<prompt-file>
set -uo pipefail

MANIFEST="${1:?usage: run_codex_batch.sh <manifest>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"

run_one() {
  local bundle="$1" out="$2" prompt="$3"
  codex exec --skip-git-repo-check "$(cat "$prompt")" < "$bundle" > "$out" 2>&1
}

pending=()
while IFS=$'\t' read -r bundle out prompt; do
  [ -z "${bundle:-}" ] && continue
  pending+=("$bundle"$'\t'"$out"$'\t'"$prompt")
done < "$MANIFEST"

attempt=1
while [ "${#pending[@]}" -gt 0 ] && [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
  if [ "$attempt" -eq 1 ]; then
    echo "=== attempt $attempt: ${#pending[@]} review(s), PARALLEL ==="
    for entry in "${pending[@]}"; do
      IFS=$'\t' read -r bundle out prompt <<< "$entry"
      run_one "$bundle" "$out" "$prompt" &
    done
    wait
  else
    echo "=== attempt $attempt: ${#pending[@]} review(s), SEQUENTIAL (the mode that works) ==="
    for entry in "${pending[@]}"; do
      IFS=$'\t' read -r bundle out prompt <<< "$entry"
      echo "    running $(basename "$out") ..."
      run_one "$bundle" "$out" "$prompt"
    done
  fi

  # Verify and keep only what failed.
  still=()
  for entry in "${pending[@]}"; do
    IFS=$'\t' read -r bundle out prompt <<< "$entry"
    if python "$HERE/verify_codex_output.py" "$out" >/dev/null 2>&1; then
      echo "    PASS  $(basename "$out")"
    else
      echo "    RERUN $(basename "$out")"
      still+=("$entry")
    fi
  done
  pending=("${still[@]+"${still[@]}"}")
  attempt=$((attempt + 1))
done

echo
if [ "${#pending[@]}" -gt 0 ]; then
  echo "!! ${#pending[@]} review(s) never produced a genuine result after $((attempt - 1)) attempts:"
  for entry in "${pending[@]}"; do
    IFS=$'\t' read -r _ out _ <<< "$entry"
    echo "   - $(basename "$out")"
  done
  echo "   Treat these as NOT REVIEWED. Do not record them as clean."
  exit 1
fi
echo "All reviews in the batch are genuine."
