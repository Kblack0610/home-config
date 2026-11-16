#!/usr/bin/env bash
# freellmapi-bootstrap.sh - bring the freellmapi app (apps/freellmapi) to its declared state through its
# admin API: the dashboard account, Unify on, auto update check off, the Premium license, and one key per
# platform in apps/freellmapi/providers.tsv whose rbw entry exists, rewritten from rbw on every run so a
# rotated key converges. Idempotent: re-run it after adding or rotating a provider key in rbw. Its state
# lives in the app's SQLite (on the PVC), so this script is how a fresh volume is rebuilt.
#
#   scripts/freellmapi-bootstrap.sh              apply, then print each key's status and what is missing
#   scripts/freellmapi-bootstrap.sh --client-key print only the unified client key (freellmapi-...) on
#                                                stdout, for apps/litellm/secret.sops.yaml FREELLMAPI_API_KEY
#
# Progress goes to stderr, so stdout carries only what a mode prints. Reaches the pod by kubectl
# port-forward on a free local port, so the first-run setup arrives from loopback and needs no setup code.
# Reads: SOPS (apps/freellmapi/secret.sops.yaml: ADMIN_EMAIL, ADMIN_PASSWORD), rbw (free_llm_api_key, the
# provider entries). Env: KUBE_CONTEXT (home-k3s).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/apps/freellmapi"
CTX="${KUBE_CONTEXT:-home-k3s}"
PREMIUM_ENTRY=free_llm_api_key
LABEL=rbw  # the label on keys this script owns; a key added by hand in the dashboard is left alone

die() { printf 'freellmapi-bootstrap: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*" >&2; }
secret() { sops -d --extract "[\"stringData\"][\"$1\"]" "$APP/secret.sops.yaml"; }

# Local port 0: kubectl picks a free one and prints it in its "Forwarding from" line, which is read
# instead of polling. The rest of its output is drained so a full pipe never stalls the forward.
coproc PF { exec kubectl --context "$CTX" -n ai-gateway port-forward svc/freellmapi :3001 2>&1; }
trap 'kill "$PF_PID" 2>/dev/null || true' EXIT
read -r -t 30 line <&"${PF[0]}" || die "port-forward did not start within 30s"
[[ "$line" =~ ^Forwarding\ from\ 127\.0\.0\.1:([0-9]+) ]] || die "port-forward: $line"
BASE="http://127.0.0.1:${BASH_REMATCH[1]}"
# A background job does not inherit the coproc fds, so drain through a duplicate.
exec {pf_out}<&"${PF[0]}"
cat <&"$pf_out" >/dev/null &

TOKEN='' REAUTH=''
api() {  # api METHOD PATH [JSON] -> body on stdout; fails on an HTTP error with the body on stderr
  local out code
  out=$(curl -sS --max-time 60 -X "$1" -H 'Content-Type: application/json' ${TOKEN:+-H "Authorization: Bearer $TOKEN"} \
    ${REAUTH:+-H "x-reauth-password: $REAUTH"} ${3:+--data-binary @-} -w '\n%{http_code}' "$BASE$2" <<<"${3:-}")
  code=${out##*$'\n'} out=${out%$'\n'*}
  [[ "$code" == 2* ]] || { printf '%s %s -> %s %s\n' "$1" "$2" "$code" "$out" >&2; return 1; }
  printf '%s' "$out"
}

curl -sS --max-time 10 -o /dev/null "$BASE/api/ping" || die "the pod does not answer /api/ping"
password=$(secret ADMIN_PASSWORD)
creds=$(jq -cn --arg e "$(secret ADMIN_EMAIL)" --arg p "$password" '{email: $e, password: $p}')
if [ "$(api GET /api/auth/status | jq -r .needsSetup)" = true ]; then
  body=$(api POST /api/auth/setup "$creds") || die "setup failed (see above)"
  say "setup: created the dashboard account"
else
  body=$(api POST /api/auth/login "$creds") || die "login failed (see above)"
fi
TOKEN=$(jq -r .token <<<"$body")
[ -n "$TOKEN" ] && [ "$TOKEN" != null ] || die "no session token"

if [ "${1:-}" = --client-key ]; then api GET /api/settings/api-key | jq -er .apiKey; exit 0; fi

api PUT /api/settings/unify '{"enabled": true}' >/dev/null || die "could not turn Unify on"
say "unify: on"
api PUT /api/settings/update-check '{"enabled": false}' >/dev/null || die "could not turn the update check off"
say "update check: off (upgrades are a PR on the image pin)"

if [ "$(api GET /api/premium | jq -r .hasKey)" = true ]; then say "premium: key already active"
else
  lic=$(rbw get "$PREMIUM_ENTRY" 2>/dev/null) || die "rbw has no $PREMIUM_ENTRY"
  api POST /api/premium/key "$(jq -cn --arg k "$lic" '{key: $k}')" >/dev/null || die "the license was refused (see above)"
  say "premium: activated"
fi

keys=$(api GET /api/keys)
missing=() failed=0
while IFS=$'\t' read -r platform entry where; do
  [ -n "$platform" ] && [ "${platform:0:1}" != "#" ] || continue
  if ! key=$(rbw get "$entry" 2>/dev/null) || [ -z "$key" ]; then missing+=("$platform: rbw add $entry  ($where)"); continue; fi
  id=$(jq -r --arg p "$platform" --arg l "$LABEL" 'first(.[] | select(.platform == $p and .label == $l) | .id) // empty' <<<"$keys")
  if [ -n "$id" ]; then
    # Rewriting a key resets its health to unknown, so only a value that differs from rbw is rewritten.
    stored=$(REAUTH=$password api POST "/api/keys/$id/reveal" | jq -r .key) || { failed=1; continue; }
    if [ "$stored" = "$key" ]; then say "key $platform: matches rbw"; continue; fi
    api PATCH "/api/keys/$id" "$(jq -cn --arg k "$key" '{key: $k}')" >/dev/null && say "key $platform: rotated to the rbw value" || failed=1
  else
    api POST /api/keys "$(jq -cn --arg p "$platform" --arg k "$key" --arg l "$LABEL" '{platform: $p, key: $k, label: $l}')" >/dev/null \
      && say "key $platform: added" || failed=1
  fi
done <"$APP/providers.tsv"

say "keys and their status:"
api GET /api/keys | jq -r '.[] | "  \(.platform)\t\(.label)\t\(.status)"' >&2
if [ ${#missing[@]} -gt 0 ]; then
  say "missing provider keys (sign up, then re-run):"
  printf '  %s\n' "${missing[@]}" >&2
fi
[ "$failed" = 0 ] || die "a provider key was refused (see above)"
