#!/bin/sh
# Offline test of .githooks/pre-push against a scratch repo built from HEAD: a pushed red is refused and
# named, a push that touches no step's paths runs only the always-on steps, the fixed push goes through,
# and a push of a commit other than HEAD runs none. Run from CI (checks.yaml) and by hand.
set -eu
src="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
out="$(mktemp)"
trap 'rm -rf "$tmp" "$out"' EXIT
git -C "$src" archive HEAD | tar -x -C "$tmp"
cd "$tmp"
unset $(git rev-parse --local-env-vars 2>/dev/null || true)
git init -q .
g() { git -c user.name=t -c user.email=t@example.invalid "$@"; }
g add -A && g commit -q -m base
base="$(git rev-parse HEAD)"
zero=0000000000000000000000000000000000000000
# The hook reads git's lines "<local ref> <local sha> <remote ref> <remote sha>" on stdin.
push() { printf 'refs/heads/x %s refs/heads/x %s\n' "$1" "$2" | PREPUSH_ALL=0 .githooks/pre-push origin >"$out" 2>&1; }
fail() { echo "FAIL: $1"; cat "$out"; exit 1; }

# A planted duplicate roster row is refused, and the output names the step that caught it.
echo "planted-dup binanceus BinHV27 aggressive base" >> apps/freqtrade/roster.tsv
g commit -q -am planted
head="$(git rev-parse HEAD)"
if push "$head" "$base"; then fail "the hook passed a planted duplicate roster row"; fi
grep -q "push refused" "$out" && grep -q "check-freqtrade-roster.py" "$out" || fail "the hook refused, but not on the roster check"

# The fixed push goes through.
g revert --no-edit HEAD >/dev/null
head="$(git rev-parse HEAD)"
push "$head" "$base" || fail "the hook refused a push with the red reverted"

# A change no row's path matches runs only the always-on steps.
echo "note" >> README.md
g commit -q -am docs
head="$(git rev-parse HEAD)"
push "$head" "$base" || fail "the hook refused a docs-only change"
grep -q "check-freqtrade-roster" "$out" && fail "a docs-only push ran the roster check"
grep -q "check-secret-store.py" "$out" || fail "a docs-only push skipped the always-on secret store check"

# A new branch (remote sha zero) is compared with the remote master: a red on it is refused.
git update-ref refs/remotes/origin/master "$base"
echo "planted-dup binanceus BinHV27 aggressive base" >> apps/freqtrade/roster.tsv
g commit -q -am planted-again
head="$(git rev-parse HEAD)"
if push "$head" "$zero"; then fail "the hook passed a planted red on a new branch"; fi

# A push of a commit other than HEAD runs no step.
if ! printf 'refs/heads/x %s refs/heads/x %s\n' "$base" "$zero" | .githooks/pre-push origin >"$out" 2>&1; then fail "a non-HEAD push was refused"; fi
grep -q "do not run" "$out" || fail "a non-HEAD push did not say it ran nothing"
echo "ok: pre-push refuses a planted red, passes the fix and a docs change, and skips a non-HEAD push"
