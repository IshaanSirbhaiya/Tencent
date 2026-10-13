#!/usr/bin/env bash
# Deploy (or update) the Walnut Internal Brain public demo on one Tencent Cloud Lighthouse VM
# (Ubuntu 22.04/24.04). Idempotent: safe to run again at any time.
#
#   ssh ubuntu@IP 'sudo bash -s' < deploy/lighthouse/deploy.sh             # first deploy / update
#   ssh ubuntu@IP 'sudo bash -s -- reset' < deploy/lighthouse/deploy.sh    # reset the demo state
#   sudo bash /opt/walnut/repo/deploy/lighthouse/deploy.sh [deploy|reset|smoke|status|logs [svc]|down]
#
# What `deploy` does:
#   1. installs git/curl/openssl and Docker Engine + compose + buildx (official Docker apt repo) if missing
#   2. adds a 2 GB swap file on small VMs (the Next.js build needs it on 2 GB RAM)
#   3. gets the source into /opt/walnut/repo: git pull if it is a clone; uses it as-is if it was
#      uploaded (upload.sh); otherwise clones REPO_URL (the repo is private: see README)
#   4. writes /opt/walnut/walnut.env (mode 600) if missing, with freshly generated keys and passcode
#   5. docker compose up -d --build, waits for health and HTTPS, then runs smoke.sh
#
# Optional env (pass through sudo, e.g. ssh ubuntu@IP 'sudo DOMAIN=demo.example.com bash -s' < deploy.sh):
#   DOMAIN            public hostname (default: <public-ip-with-dashes>.sslip.io)
#   TOKENHUB_API_KEY  Hunyuan via TokenHub (prefer editing the env file over the command line)
#   IB_DEMO_PASSCODE  set/rotate the demo passcode
#   REPO_URL BRANCH   source to clone (default https://github.com/IshaanSirbhaiya/Tencent.git, Tencent)
#   NO_BUILD=1        do not build; use images already loaded with `docker load` (see README)
#   SKIP_SMOKE=1      do not run smoke.sh at the end
set -euo pipefail

ACTION="${1:-deploy}"; shift || true
WALNUT_HOME="${WALNUT_HOME:-/opt/walnut}"
SRC="${SRC:-$WALNUT_HOME/repo}"
ENV_FILE="${ENV_FILE:-$WALNUT_HOME/walnut.env}"
REPO_URL="${REPO_URL:-https://github.com/IshaanSirbhaiya/Tencent.git}"
BRANCH="${BRANCH:-Tencent}"
PROJECT="${COMPOSE_PROJECT:-walnut}"
COMPOSE_FILE="$SRC/deploy/lighthouse/docker-compose.yml"

log()  { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root: ssh ubuntu@IP 'sudo bash -s' < deploy.sh"
export DEBIAN_FRONTEND=noninteractive

dc() { docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }

env_get() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -n1; }
env_set() {  # upsert KEY=VALUE in the env file
  local k="$1" v="$2" tmp
  tmp="$(mktemp "$WALNUT_HOME/.env.XXXXXX")"
  { grep -v "^$k=" "$ENV_FILE" 2>/dev/null || true; printf '%s=%s\n' "$k" "$v"; } > "$tmp"
  chmod 600 "$tmp"; mv "$tmp" "$ENV_FILE"
}

# --- 1. packages and Docker ----------------------------------------------------------------------
install_prereqs() {
  local need=()
  for c in git curl openssl; do command -v "$c" >/dev/null 2>&1 || need+=("$c"); done
  command -v gpg >/dev/null 2>&1 || need+=(gnupg)
  if [ "${#need[@]}" -gt 0 ]; then
    log "installing ${need[*]}"
    apt-get update -y -q && apt-get install -y -q ca-certificates "${need[@]}"
  fi
}

docker_repo() {
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck disable=SC1091
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y -q
}

install_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    log "installing Docker Engine (official apt repo)"
    docker_repo
    apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
  if ! docker compose version >/dev/null 2>&1 || ! docker buildx version >/dev/null 2>&1; then
    log "installing the docker compose and buildx plugins"
    apt-get install -y -q docker-compose-v2 docker-buildx >/dev/null 2>&1 \
      || { docker_repo && apt-get install -y -q docker-buildx-plugin docker-compose-plugin; }
  fi
  systemctl enable --now docker >/dev/null 2>&1 || true
  docker compose version >/dev/null 2>&1 || die "docker compose v2 is not available"
  docker buildx version >/dev/null 2>&1 || die "docker buildx (BuildKit) is not available"
}

ensure_swap() {
  local mem_mb; mem_mb="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)"
  if [ "$mem_mb" -lt 3500 ] && [ -z "$(swapon --show --noheadings 2>/dev/null)" ]; then
    log "adding a 2 GB swap file (RAM ${mem_mb} MB)"
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
}

# --- 2. source ------------------------------------------------------------------------------------
get_source() {
  mkdir -p "$WALNUT_HOME"
  if [ -d "$SRC/.git" ]; then
    log "updating $SRC ($BRANCH)"
    git -C "$SRC" pull --ff-only origin "$BRANCH" || die "git pull failed (local changes on the VM?)"
  elif [ -f "$COMPOSE_FILE" ]; then
    log "using the uploaded source in $SRC (no .git: upload.sh)"
  else
    log "cloning $REPO_URL ($BRANCH) into $SRC"
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$SRC" \
      || die "clone failed. The repo is private: run deploy/lighthouse/upload.sh from your laptop instead, or set REPO_URL to an ssh URL with a read-only deploy key (README)."
  fi
  [ -f "$COMPOSE_FILE" ] || die "$COMPOSE_FILE not found: is deploy/lighthouse committed on $BRANCH?"
}

# --- 3. env file ----------------------------------------------------------------------------------
public_ip() {
  curl -fsS --max-time 3 http://metadata.tencentyun.com/latest/meta-data/public-ipv4 2>/dev/null \
    || curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null \
    || curl -fsS --max-time 5 https://ifconfig.me 2>/dev/null || true
}

ensure_env() {
  mkdir -p "$WALNUT_HOME"; chmod 700 "$WALNUT_HOME"
  if [ ! -f "$ENV_FILE" ]; then
    log "writing $ENV_FILE with fresh keys"
    local domain="${DOMAIN:-}" ip
    if [ -z "$domain" ]; then
      ip="$(public_ip)"
      [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "could not detect the public IP; rerun with DOMAIN=<host>"
      domain="${ip//./-}.sslip.io"
    fi
    umask 077
    cat > "$ENV_FILE" <<EOF
# Walnut Internal Brain demo env (generated $(date -u +%FT%TZ) by deploy.sh). Keep secret. Mode 600.
# Template with explanations: deploy/lighthouse/.env.example. Synthetic data only: never add real
# Slack/Atlassian/Google credentials here.
DOMAIN=$domain
HTTP_PORT=80
HTTPS_PORT=443
IB_DEMO_PASSCODE=${IB_DEMO_PASSCODE:-$(openssl rand -hex 8)}
IB_TOKEN_KEY=$(openssl rand -hex 32)
IB_LEDGER_KEY=$(openssl rand -hex 32)
MOCK_SVC_TOKEN=$(openssl rand -hex 32)
MOCK_ADMIN_TOKEN=$(openssl rand -hex 32)
MOCK_WEBHOOK_SECRET=$(openssl rand -hex 32)
TOKENHUB_API_KEY=${TOKENHUB_API_KEY:-}
TOKENHUB_BASE=https://tokenhub-intl.tencentcloudmaas.com/v1
IB_FORCE_OFFLINE=0
IB_POLL_S=120
IB_ANCHOR_INTERVAL_S=900
IB_ANCHOR_FILE=
EOF
  else
    # explicit overrides passed on this run win; generated secrets are never regenerated
    [ -n "${DOMAIN:-}" ] && env_set DOMAIN "$DOMAIN"
    [ -n "${TOKENHUB_API_KEY:-}" ] && env_set TOKENHUB_API_KEY "$TOKENHUB_API_KEY"
    [ -n "${IB_DEMO_PASSCODE:-}" ] && env_set IB_DEMO_PASSCODE "$IB_DEMO_PASSCODE"
  fi
  chown root:root "$ENV_FILE"; chmod 600 "$ENV_FILE"
  local k v
  for k in DOMAIN IB_DEMO_PASSCODE IB_TOKEN_KEY IB_LEDGER_KEY MOCK_SVC_TOKEN MOCK_ADMIN_TOKEN MOCK_WEBHOOK_SECRET; do
    v="$(env_get "$k")"
    [ -n "$v" ] || die "$k is empty in $ENV_FILE"
    case "$v" in replace-with*) die "$k in $ENV_FILE is still a placeholder";; esac
  done
  for k in IB_TOKEN_KEY IB_LEDGER_KEY; do
    v="$(env_get "$k")"
    [ "${#v}" -ge 32 ] || die "$k must be at least 32 characters"
  done
  [ "$(env_get MOCK_SVC_TOKEN)" != "$(env_get MOCK_ADMIN_TOKEN)" ] || die "MOCK_SVC_TOKEN and MOCK_ADMIN_TOKEN must differ"
}

# --- 4. run ---------------------------------------------------------------------------------------
health_of() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$(dc ps -q "$1" 2>/dev/null)" 2>/dev/null || echo missing; }

wait_healthy() {
  local svc deadline=$(( $(date +%s) + ${WAIT_S:-900} )) st
  for svc in mock ib web; do
    printf 'waiting for %s ' "$svc"
    while :; do
      st="$(health_of "$svc")"
      [ "$st" = healthy ] && { echo "healthy"; break; }
      [ "$st" = unhealthy ] && { echo; dc logs --tail 80 "$svc"; die "$svc is unhealthy"; }
      [ "$(date +%s)" -lt "$deadline" ] || { echo; dc logs --tail 80 "$svc"; die "timed out waiting for $svc"; }
      printf '.'; sleep 5
    done
  done
  local domain url; domain="$(env_get DOMAIN)"; url="https://$domain/login"
  printf 'waiting for HTTPS on %s ' "$url"
  local k=(); [ "$domain" = localhost ] && k=(-k)
  while ! curl -fsS "${k[@]}" --max-time 10 -o /dev/null "$url" 2>/dev/null; do
    [ "$(date +%s)" -lt "$deadline" ] || { echo; dc logs --tail 80 caddy; die "HTTPS not up (certificate? ports 80/443 open in the Lighthouse firewall?)"; }
    printf '.'; sleep 5
  done
  echo "up"
}

run_smoke() {
  [ "${SKIP_SMOKE:-0}" = 1 ] && return 0
  log "smoke test"
  local domain ip; domain="$(env_get DOMAIN)"; ip="$(public_ip)"
  ENV_FILE="$ENV_FILE" PORT_CHECK_HOST="${PORT_CHECK_HOST:-${ip:-$domain}}" COMPOSE_PROJECT="$PROJECT" \
    SMOKE_INSECURE="$([ "$domain" = localhost ] && echo 1 || echo 0)" \
    bash "$SRC/deploy/lighthouse/smoke.sh"
}

summary() {
  cat <<EOF

------------------------------------------------------------------------------
 Walnut Internal Brain demo is up:  https://$(env_get DOMAIN)
 Demo passcode:                     $(env_get IB_DEMO_PASSCODE)
 Model:                             $([ -n "$(env_get TOKENHUB_API_KEY)" ] && echo "TokenHub (hy3 -> deepseek-v4-flash -> extractive)" || echo "extractive only (no TOKENHUB_API_KEY)")
 Env file (secrets, mode 600):      $ENV_FILE
 Status / logs:                     sudo bash $SRC/deploy/lighthouse/deploy.sh status | logs ib
 Reset the demo:                    sudo bash $SRC/deploy/lighthouse/deploy.sh reset
------------------------------------------------------------------------------
EOF
}

case "$ACTION" in
  deploy)
    install_prereqs; install_docker; ensure_swap; get_source; ensure_env
    log "starting the stack (first build takes several minutes)"
    if [ "${NO_BUILD:-0}" = 1 ]; then dc up -d --no-build --pull never --remove-orphans
    else dc up -d --build --remove-orphans; fi
    wait_healthy; run_smoke; summary ;;
  reset)
    # Fresh synthetic world and an empty ledger/index. TLS certificates (caddy-data) are kept.
    log "resetting demo state (ledger, index, answer store, mock world)"
    dc stop web ib mock || true
    dc rm -f web ib mock || true
    docker volume rm "${PROJECT}_ib-var" 2>/dev/null || true
    dc up -d --no-build
    wait_healthy; run_smoke; summary ;;
  smoke)  run_smoke ;;
  status) dc ps ;;
  logs)   dc logs --tail "${TAIL:-200}" "$@" ;;
  down)   dc down ;;
  *) die "unknown action '$ACTION' (deploy | reset | smoke | status | logs [service] | down)" ;;
esac
