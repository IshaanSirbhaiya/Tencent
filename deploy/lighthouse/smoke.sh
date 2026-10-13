#!/usr/bin/env bash
# Smoke test of the public Walnut Internal Brain demo, through the PUBLIC URL only.
#
#   bash smoke.sh                                   # on the VM: reads /opt/walnut/walnut.env
#   BASE_URL=https://43-134-10-20.sslip.io IB_DEMO_PASSCODE=... bash smoke.sh   # from your laptop
#
# Env:
#   BASE_URL          public URL (default https://$DOMAIN)
#   IB_DEMO_PASSCODE  demo passcode (default: from ENV_FILE)
#   ENV_FILE          env file to read DOMAIN / IB_DEMO_PASSCODE from (default /opt/walnut/walnut.env)
#   PORT_CHECK_HOST   host whose 8100/8200/3100 must be closed (default: the BASE_URL host; skipped
#                     for localhost so a local test never touches dev servers on those ports)
#   SMOKE_INSECURE=1  accept a self-signed cert (local test with DOMAIN=localhost)
#   WORLD             path to world.json for the leak-canary check (default: found next to this repo)
#
# Exit 0 only if every check passes. Needs bash, curl and python3 (or python).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo .)"
ENV_FILE="${ENV_FILE:-/opt/walnut/walnut.env}"

env_get() {  # read KEY=value from the env file without executing it
  [ -r "$ENV_FILE" ] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n1 | tr -d '\r' | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

DOMAIN="${DOMAIN:-$(env_get DOMAIN)}"
IB_DEMO_PASSCODE="${IB_DEMO_PASSCODE:-$(env_get IB_DEMO_PASSCODE)}"
BASE_URL="${BASE_URL:-https://${DOMAIN}}"
BASE_URL="${BASE_URL%/}"
if [ "$BASE_URL" = "https://" ]; then
  echo "smoke: set BASE_URL (or DOMAIN), or run on the VM where $ENV_FILE exists" >&2; exit 2
fi
if [ -z "$IB_DEMO_PASSCODE" ]; then
  echo "smoke: IB_DEMO_PASSCODE is not set (and not found in $ENV_FILE)" >&2; exit 2
fi

PY=""
for c in python3 python py; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import json' >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "smoke: python3 is required for JSON checks" >&2; exit 2; }

CURL=(curl -sS --max-time 30)
[ "${SMOKE_INSECURE:-0}" = "1" ] && CURL+=(-k)
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# The constant refusal (databridge_ib/models.py REFUSAL_TEXT); read from the repo when it is there.
REFUSAL="I couldn't find an answer to that in the sources you can access."
MODELS="$HERE/../../databridge/apps/internal_brain/databridge_ib/models.py"
if [ -r "$MODELS" ]; then
  r="$(sed -n 's/^REFUSAL_TEXT = "\(.*\)"$/\1/p' "$MODELS" | head -n1)"; [ -n "$r" ] && REFUSAL="$r"
fi
# Demo questions (synthetic world, fixtures/enterprise/world.json: S1-q1 and S3-q1).
Q_S1="What's the status of the database migration project and were there any blockers raised in Slack last week?"
Q_S3="Show me the security incident report from the Q3 breach."
WORLD="${WORLD:-$HERE/../../databridge/fixtures/enterprise/world.json}"

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); printf '  PASS  %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }

status_of() {  # METHOD PATH [curl args...] -> prints HTTP status
  local m="$1" p="$2"; shift 2
  "${CURL[@]}" -o /dev/null -w '%{http_code}' -X "$m" "$@" "$BASE_URL$p" 2>/dev/null || echo 000
}

json() {  # FILE EXPR -> evaluates EXPR with d = parsed JSON
  "$PY" - "$1" "$2" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("<not json>"); sys.exit(0)
print(eval(sys.argv[2], {"d": d, "len": len}))
PY
}

login() {  # PERSONA JAR -> HTTP status; headers in $TMP/login.h
  "${CURL[@]}" -o "$TMP/login.json" -D "$TMP/login.h" -w '%{http_code}' -c "$2" \
    -H 'content-type: application/json' -X POST "$BASE_URL/api/ib/auth/demo-login" \
    --data "$("$PY" -c 'import json,sys; print(json.dumps({"persona": sys.argv[1], "passcode": sys.argv[2]}))' "$1" "$IB_DEMO_PASSCODE")" \
    2>/dev/null || echo 000
}

ask() {  # JAR QUESTION OUTFILE -> HTTP status
  "${CURL[@]}" --max-time 150 -o "$3" -w '%{http_code}' -b "$1" -H 'content-type: application/json' \
    -X POST "$BASE_URL/api/ib/ib/ask" \
    --data "$("$PY" -c 'import json,sys; print(json.dumps({"question": sys.argv[1]}))' "$2")" 2>/dev/null || echo 000
}

echo "Smoke test against $BASE_URL"

echo "[edge]"
s="$("${CURL[@]}" -o /dev/null -D "$TMP/login_page.h" -w '%{http_code}' "$BASE_URL/login" 2>/dev/null || echo 000)"
[ "$s" = 200 ] && ok "GET /login is 200 over HTTPS" || bad "GET /login is 200 over HTTPS" "got $s"
grep -qi '^strict-transport-security:' "$TMP/login_page.h" && ok "HSTS header present" || bad "HSTS header present"
grep -qi '^content-security-policy:' "$TMP/login_page.h" && ok "CSP header present" || bad "CSP header present"
if grep -qi '^server:' "$TMP/login_page.h"; then bad "no Server header"; else ok "no Server header"; fi

hostport="${BASE_URL#https://}"; hostport="${hostport%%/*}"; host="${hostport%%:*}"
if [ "$hostport" = "$host" ] && [ "${SMOKE_INSECURE:-0}" != "1" ]; then
  s="$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "http://$host/login" 2>/dev/null || echo 000)"
  case "$s" in 301|302|307|308) ok "plain HTTP redirects to HTTPS ($s)";; *) bad "plain HTTP redirects to HTTPS" "got $s";; esac
fi

s="$("${CURL[@]}" -o "$TMP/health.json" -w '%{http_code}' "$BASE_URL/api/ib/health" 2>/dev/null || echo 000)"
[ "$s" = 200 ] && [ "$(json "$TMP/health.json" 'd.get("ok")')" = True ] \
  && ok "web -> IB health via /api/ib/health" || bad "web -> IB health via /api/ib/health" "got $s"

echo "[nothing else is public]"
for p in /health /docs /openapi.json /auth/whoami /ib/sources /audit/verify /sim/reset \
         /admin/webhooks /slack/api/conversations.list /confluence/wiki/api/v2/spaces /jira/rest/api/3/search/jql /drive/v3/files; do
  s="$(status_of GET "$p")"; [ "$s" = 404 ] && ok "GET $p is 404 at the edge" || bad "GET $p is 404 at the edge" "got $s"
done
for p in /webhooks/slack /admin/reset /api/ib/webhooks/slack; do
  s="$(status_of POST "$p" -H 'content-type: application/json' --data '{}')"
  [ "$s" = 404 ] && ok "POST $p is 404" || bad "POST $p is 404" "got $s"
done

echo "[sign-in]"
s="$("${CURL[@]}" -o /dev/null -w '%{http_code}' -H 'content-type: application/json' -X POST \
      "$BASE_URL/api/ib/auth/demo-login" --data '{"persona":"jdoe"}' 2>/dev/null || echo 000)"
[ "$s" = 401 ] && ok "demo-login without the passcode is refused (401)" || bad "demo-login without the passcode is refused (401)" "got $s"
s="$("${CURL[@]}" -o /dev/null -w '%{http_code}' -H 'content-type: application/json' -X POST \
      "$BASE_URL/api/ib/auth/demo-login" --data '{"persona":"jdoe","passcode":"wrong-passcode"}' 2>/dev/null || echo 000)"
[ "$s" = 401 ] && ok "demo-login with a wrong passcode is refused (401)" || bad "demo-login with a wrong passcode is refused (401)" "got $s"
s="$(ask /dev/null "$Q_S1" "$TMP/anon.json")"
[ "$s" = 401 ] && ok "ask without a session is 401" || bad "ask without a session is 401" "got $s"

JAR_J="$TMP/jdoe.jar"
s="$(login jdoe "$JAR_J")"
[ "$s" = 200 ] && ok "demo-login as jdoe with the passcode" || bad "demo-login as jdoe with the passcode" "got $s"
ck="$(grep -i '^set-cookie: *ib_session=' "$TMP/login.h" || true)"
if [ -n "$ck" ] && echo "$ck" | grep -qi 'httponly' && echo "$ck" | grep -qi 'secure'; then
  ok "session cookie is HttpOnly and Secure"
else
  bad "session cookie is HttpOnly and Secure" "${ck:-no ib_session cookie}"
fi

echo "[S1: unified answer with citations, as jdoe]"
s="$(ask "$JAR_J" "$Q_S1" "$TMP/s1.json")"
n="$(json "$TMP/s1.json" 'len(d.get("citations") or [])')"
if [ "$s" = 200 ] && [ "${n:-0}" -ge 1 ] 2>/dev/null; then ok "S1 answered with $n citation(s)"; else bad "S1 answered with at least one citation" "HTTP $s, citations=$n"; fi
[ "$(json "$TMP/s1.json" 'd.get("answer")')" != "$REFUSAL" ] && ok "S1 is not the refusal" || bad "S1 is not the refusal"
echo "        model: $(json "$TMP/s1.json" '(d.get("model") or {}).get("model") or (d.get("model") or {})')"
if [ -r "$WORLD" ]; then  # the private-channel blocker must never reach jdoe (fact S1-blocker-private)
  leak="$("$PY" - "$WORLD" "$TMP/s1.json" <<'PY'
import json, sys
w = json.load(open(sys.argv[1], encoding="utf-8")); r = json.load(open(sys.argv[2], encoding="utf-8"))
f = next((f for f in w.get("facts", []) if f.get("id") == "S1-blocker-private"), None)
blob = json.dumps(r)
print("skip" if not f else ("LEAK" if (f.get("canary") and f["canary"] in blob) or f["statement"] in blob else "clean"))
PY
)"
  case "$leak" in clean) ok "S1 answer omits the private-channel blocker (canary absent)";;
                  skip) echo "  SKIP  canary check (fact not in world.json)";;
                  *) bad "S1 answer omits the private-channel blocker" "$leak";; esac
fi

echo "[S3: contractor gets the constant refusal, as ken.ext]"
JAR_K="$TMP/ken.jar"
s="$(login ken.ext "$JAR_K")"
[ "$s" = 200 ] && ok "demo-login as ken.ext" || bad "demo-login as ken.ext" "got $s"
s="$(ask "$JAR_K" "$Q_S3" "$TMP/s3.json")"
ans="$(json "$TMP/s3.json" 'd.get("answer")')"
n="$(json "$TMP/s3.json" 'len(d.get("citations") or [])')"
if [ "$s" = 200 ] && [ "$ans" = "$REFUSAL" ] && [ "$n" = 0 ]; then ok "S3 answer is exactly the constant refusal, no citations"
else bad "S3 answer is exactly the constant refusal, no citations" "HTTP $s, citations=$n, answer=$ans"; fi

echo "[logout]"
s="$("${CURL[@]}" -o /dev/null -w '%{http_code}' -b "$JAR_J" -X POST "$BASE_URL/api/ib/auth/logout" 2>/dev/null || echo 000)"
s2="$("${CURL[@]}" -o /dev/null -w '%{http_code}' -b "$JAR_J" "$BASE_URL/api/ib/auth/whoami" 2>/dev/null || echo 000)"
[ "$s" = 200 ] && [ "$s2" = 401 ] && ok "logout revokes the session (whoami then 401)" || bad "logout revokes the session" "logout $s, whoami $s2"

echo "[internal ports are closed from outside]"
PORT_CHECK_HOST="${PORT_CHECK_HOST:-$host}"
case "$PORT_CHECK_HOST" in
  localhost|127.*|::1|"") echo "  SKIP  direct port check against $PORT_CHECK_HOST (local test; see the docker check below)";;
  *) for p in 8100 8200 3100; do
       if curl -sS --connect-timeout 5 --max-time 8 -o /dev/null "http://$PORT_CHECK_HOST:$p/health" 2>/dev/null; then
         bad "port $p on $PORT_CHECK_HOST is NOT reachable" "it answered"
       else ok "port $p on $PORT_CHECK_HOST is not reachable"; fi
     done;;
esac
if command -v docker >/dev/null 2>&1 && docker ps >/dev/null 2>&1; then
  pub="$(docker ps --filter label=com.docker.compose.project="${COMPOSE_PROJECT:-walnut}" --format '{{.Label "com.docker.compose.service"}} {{.Ports}}' \
          | awk '$1!="caddy" && $0 ~ /->/')"
  if [ -n "$(docker ps -q --filter label=com.docker.compose.project="${COMPOSE_PROJECT:-walnut}")" ]; then
    [ -z "$pub" ] && ok "only caddy publishes host ports (docker)" || bad "only caddy publishes host ports (docker)" "$pub"
  fi
fi

echo
echo "Smoke: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
