#!/usr/bin/env bash
# Mint a binks personal access token (bk_pat_...) for one user, through the
# admin path: straight into the binks Postgres by kubectl exec. The token goes
# into a SOPS file under the key BINKS_DEMO_TOKEN and is never printed. The
# database keeps only its sha256, the way Settings > Integrations does
# (platform apps/binks/api src/client-auth/manage.ts).
#
#   scripts/binks-mint-pat.sh <email> <token-name> <days> <sops-file>
#
# One run is one rotation: the user is created when missing, the new token is
# stored, and every older token with the same name for that user is revoked,
# all in one database transaction. The SOPS file is encrypted first (a broken
# sops aborts before the database is touched) and moved into place last.
# Steps and troubleshooting: apps/binks-api/README.md, "Binks demo token".
set -euo pipefail

usage='usage: binks-mint-pat.sh <email> <token-name> <days> <sops-file>'
email="${1:?$usage}"
name="${2:?$usage}"
days="${3:?$usage}"
out="${4:?$usage}"
ctx="${KUBE_CONTEXT:-home-k3s}"

case "$days" in '' | *[!0-9]*) echo "days must be a whole number" >&2; exit 2 ;; esac
[ "$days" -ge 1 ] || { echo "days must be at least 1" >&2; exit 2; }

# Same shape the app mints: bk_pat_ + 32 random bytes as base64url.
secret="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n')"
token="bk_pat_${secret}"
hash="$(printf %s "$token" | sha256sum | cut -d' ' -f1)"
prefix="${token:0:11}"

# Encrypt first, into a temp file beside the target, so a sops failure leaves
# both the old file and the database untouched.
tmp="$(mktemp "${out}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
printf 'BINKS_DEMO_TOKEN: %s\n' "$token" |
  sops -e --filename-override "$out" --input-type yaml --output-type yaml /dev/stdin >"$tmp"

# The SQL goes in on stdin, so no value shows in a process list. Values are
# psql variables, quoted by psql (:'var'), never spliced. -1 is one transaction.
kubectl --context "$ctx" -n binks-api exec -i binks-postgres-0 -- sh -c \
  'psql -U "$POSTGRES_USER" -d "${POSTGRES_DB:-$POSTGRES_USER}" -1 -v ON_ERROR_STOP=1 -q -At \
     -v email="$1" -v name="$2" -v hash="$3" -v prefix="$4" -v days="$5"' sh \
  "$email" "$name" "$hash" "$prefix" "$days" >/dev/null <<'SQL'
INSERT INTO users (email)
SELECT :'email' WHERE NOT EXISTS (SELECT 1 FROM users WHERE email = :'email');
INSERT INTO personal_access_tokens (user_id, name, token_hash, prefix, expires_at)
SELECT id, :'name', :'hash', :'prefix', CURRENT_TIMESTAMP + (:'days' || ' days')::interval
FROM users WHERE email = :'email';
UPDATE personal_access_tokens SET revoked_at = CURRENT_TIMESTAMP
WHERE name = :'name' AND token_hash <> :'hash' AND revoked_at IS NULL
  AND user_id = (SELECT id FROM users WHERE email = :'email');
SQL

mv "$tmp" "$out"
trap - EXIT
echo "minted $name for $email, expires in $days days, written to $out" >&2
