# Sourced by demo_up.sh (and by its tests). Keeps every demo wipe away from the stand-in model's
# spend ledger (databridge/var/model_guard, or IB_MODEL_GUARD_DIR; D-42): a reset must never
# refresh the model budget. Run from the databridge/ directory.

# An absolute path: the longest existing prefix resolved physically, the rest kept as written.
_wg_abs() {
  local p="$1" head tail=""
  if command -v realpath >/dev/null 2>&1 && realpath -m / >/dev/null 2>&1; then
    realpath -m -- "$p"; return
  fi
  case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
  head="$p"
  while [ ! -d "$head" ]; do tail="/$(basename "$head")$tail"; head="$(dirname "$head")"; done
  printf '%s%s\n' "$(cd "$head" && pwd -P)" "$tail"
}

# The guard dir the Brain will use: IB_MODEL_GUARD_DIR (the file wins over the shell, as in the
# Brain's own subshell), else var/model_guard. A path only; nothing else leaves the subshell.
model_guard_dir() {
  ( set +eu
    if [ -f .env.ib.local ]; then set -a; . ./.env.ib.local </dev/null >/dev/null 2>&1; set +a; fi
    printf '%s\n' "${IB_MODEL_GUARD_DIR:-var/model_guard}" )
}

# refuse_guard_overlap STATE: non-zero (and a message) when STATE is the guard dir, contains it or
# sits inside it. Wiping such a STATE would reset the model budget, so it is never done.
refuse_guard_overlap() {
  local s g
  s="$(_wg_abs "$1")"; g="$(_wg_abs "$(model_guard_dir)")"
  case "$g/" in "$s"/*)
    echo "refusing to use $1 as the demo state: it is or contains the model spend ledger ($g)" >&2
    return 2 ;;
  esac
  case "$s/" in "$g"/*)
    echo "refusing to use $1 as the demo state: it is inside the model spend ledger dir ($g)" >&2
    return 2 ;;
  esac
  return 0
}
