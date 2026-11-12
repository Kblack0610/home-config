#!/usr/bin/env bash
# freellmapi-bootstrap.sh - bring the freellmapi app (apps/freellmapi) to its declared state through its
# admin API: the dashboard account, Unify on, auto update check off, the Premium license, and one key per
# platform in apps/freellmapi/providers.tsv whose rbw entry exists. Idempotent: re-run it after adding a
# provider key to rbw. Its state lives in the app's SQLite (on the PVC), so this script is how a fresh
# volume is rebuilt.
#
#   scripts/freellmapi-bootstrap.sh              apply, then print what is configured and what is missing
#   scripts/freellmapi-bootstrap.sh --client-key print the unified client key (freellmapi-...) and exit,
#                                                for apps/litellm/secret.sops.yaml FREELLMAPI_API_KEY
#
# Reaches the pod by kubectl port-forward, so the first-run setup arrives from loopback and needs no
# setup code. Reads: SOPS (apps/freellmapi/secret.sops.yaml: ADMIN_EMAIL, ADMIN_PASSWORD), rbw
# (free_llm_api_key, the provider entries). Env: KUBE_CONTEXT (home-k3s), FREELLMAPI_LOCAL_PORT (13001).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/apps/freellmapi"
CTX="${KUBE_CONTEXT:-home-k3s}"
PORT="${FREELLMAPI_LOCAL_PORT:-13001}"
BASE="http://127.0.0.1:$PORT"
PREMIUM_ENTRY=free_llm_api_key

die() { printf 'freellmapi-bootstrap: %s\n' "$*" >&2; exit 1; }
secret() { sops -d --extract "[\"stringData\"][\"$1\"]" "$APP/secret.sops.yaml"; }

# The port-forward prints "Forwarding from" once it listens: read that line instead of polling the port.
coproc PF { exec kubectl --context "$CTX" -n ai-gateway port-forward svc/freellmapi "$PORT:3001" 2>&1; }
trap 'kill "$PF_PID" 2>/dev/null || true' EXIT
read -r -t 30 line <&"${PF[0]}" || die "port-forward did not start within 30s"
[[ "$line" == Forwarding\ from* ]] || die "port-forward: $line"

TOKEN=''
api() {  # api METHOD PATH [JSON] -> body on stdout; fails on an HTTP error with the body on stderr
  local out code
  out=$(curl -sS --max-time 60 -X "$1" -H 'Content-Type: application/json' ${TOKEN:+-H "Authorization: Bearer $TOKEN"} \
    ${3:+--data-binary @-} -w '\n%{http_code}' "$BASE$2" <<<"${3:-}")
  code=${out##*$'\n'} out=${out%$'\n'*}
  [[ "$code" == 2* ]] || { printf '%s %s -> %s %s\n' "$1" "$2" "$code" "$out" >&2; return 1; }
  printf '%s' "$out"
}

curl -sS --max-time 10 -o /dev/null "$BASE/api/ping" || die "the pod does not answer /api/ping"
creds=$(jq -cn --arg e "$(secret ADMIN_EMAIL)" --arg p "$(secret ADMIN_PASSWORD)" '{email: $e, password: $p}')
if body=$(api POST /api/auth/setup "$creds" 2>/dev/null); then echo "setup: created the dashboard account"
else body=$(api POST /api/auth/login "$creds") || die "login failed"; fi
TOKEN=$(jq -r .token <<<"$body")
[ -n "$TOKEN" ] && [ "$TOKEN" != null ] || die "no session token"

if [ "${1:-}" = --client-key ]; then api GET /api/settings/api-key | jq -r .apiKey; exit 0; fi

api PUT /api/settings/unify '{"enabled": true}' >/dev/null && echo "unify: on"
api PUT /api/settings/update-check '{"enabled": false}' >/dev/null && echo "update check: off (upgrades are a PR on the image pin)"

if [ "$(api GET /api/premium | jq -r '.hasKey')" = true ]; then echo "premium: key already active"
else
  lic=$(rbw get "$PREMIUM_ENTRY" 2>/dev/null) || die "rbw has no $PREMIUM_ENTRY"
  api POST /api/premium/key "$(jq -cn --arg k "$lic" '{key: $k}')" >/dev/null && echo "premium: activated"
fi

have=$(api GET /api/keys | jq -r '[.. | objects | select(has("platform")) | .platform] | unique | .[]')
missing=()
while IFS=$'\t' read -r platform entry where; do
  [ -n "$platform" ] && [ "${platform:0:1}" != "#" ] || continue
  if grep -qx "$platform" <<<"$have"; then echo "key $platform: present"; continue; fi
  if ! key=$(rbw get "$entry" 2>/dev/null) || [ -z "$key" ]; then missing+=("$platform: rbw add $entry  ($where)"); continue; fi
  if api POST /api/keys "$(jq -cn --arg p "$platform" --arg k "$key" '{platform: $p, key: $k, label: "rbw"}')" >/dev/null; then
    echo "key $platform: added"
  else echo "key $platform: refused (see above)"; fi
done <"$APP/providers.tsv"

echo "models served: $(api GET /api/models | jq '[.. | objects | select(has("enabled") and .enabled == true)] | length') enabled rows"
if [ ${#missing[@]} -gt 0 ]; then
  echo "missing provider keys (sign up, then re-run):"
  printf '  %s\n' "${missing[@]}"
fi
