#!/usr/bin/env bash
# Mint a binks personal access token (bk_pat_...) for one user, through the
# admin path: straight into the binks Postgres by kubectl exec. Prints the
# token once on stdout and nothing else; the database keeps only its sha256,
# the same way Settings > Integrations does (platform apps/binks/api
# src/client-auth/manage.ts). The user row is created when it does not exist.
#
#   scripts/binks-mint-pat.sh <email> <token-name> [days] | sops --set ...
#
# Steps and where the result goes: docs/credentials.md, "binks API tokens".
set -euo pipefail

email="${1:?usage: binks-mint-pat.sh <email> <token-name> [days]}"
name="${2:?usage: binks-mint-pat.sh <email> <token-name> [days]}"
days="${3:-365}"
ctx="${KUBE_CONTEXT:-home-k3s}"

case "$days" in '' | *[!0-9]*) echo "days must be a whole number" >&2; exit 2 ;; esac

# Same shape the app mints: bk_pat_ + 32 random bytes as base64url.
secret="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n')"
token="bk_pat_${secret}"
hash="$(printf %s "$token" | sha256sum | cut -d' ' -f1)"
prefix="${token:0:11}"

# The SQL goes in on stdin, so no value shows in a process list. The email and
# name are passed as psql variables and quoted by psql (:'var'), never spliced.
kubectl --context "$ctx" -n binks-api exec -i binks-postgres-0 -- sh -c \
  'psql -U "$POSTGRES_USER" -d "${POSTGRES_DB:-$POSTGRES_USER}" -v ON_ERROR_STOP=1 -q -At \
     -v email="$1" -v name="$2" -v hash="$3" -v prefix="$4" -v days="$5"' sh \
  "$email" "$name" "$hash" "$prefix" "$days" >/dev/null <<'SQL'
INSERT INTO users (email)
SELECT :'email' WHERE NOT EXISTS (SELECT 1 FROM users WHERE email = :'email');
INSERT INTO personal_access_tokens (user_id, name, token_hash, prefix, expires_at)
SELECT id, :'name', :'hash', :'prefix', CURRENT_TIMESTAMP + (:'days' || ' days')::interval
FROM users WHERE email = :'email';
SQL

printf '%s\n' "$token"
