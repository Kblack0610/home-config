#!/bin/sh
# Offline test of ensure-build-keychain.sh: a stub `security` over a scratch state dir. CI runs it
# in the checks workflow.
set -u
here=$(cd "$(dirname "$0")" && pwd)
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
mkdir "$t/bin" "$t/home"
fail=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }

KC="$t/home/Library/Keychains/bnb mac build.keychain-db"
LOGIN="$t/home/Library/Keychains/login.keychain-db"
S="$t/state"; mkdir "$S"
# create-keychain makes the file, unlock-keychain records the password it was given, list-keychains
# reads and rewrites $S/list in the real command's output format.
cat > "$t/bin/security" <<'STUB'
#!/bin/sh
echo "$*" >> "$S/calls"
case "$1" in
create-keychain) mkdir -p "$(dirname "$4")"; : > "$4" ;;
set-keychain-settings) [ "$#" -eq 2 ] && [ -f "$S/unlocked" ] || exit 1 ;;
unlock-keychain) echo "$3" > "$S/unlocked" ;;
list-keychains)
    if [ "${4:-}" = "-s" ]; then shift 4; : > "$S/list"; for k in "$@"; do echo "$k" >> "$S/list"; done
    else while IFS= read -r k; do printf '    "%s"\n' "$k"; done < "$S/list"; fi ;;
esac
STUB
chmod +x "$t/bin/security"
mkdir -p "$(dirname "$LOGIN")"; : > "$LOGIN"
cat > "$t/env" <<ENV
MAC_SIGN_KEYCHAIN="$KC"
MAC_SIGN_KEYCHAIN_PASSWORD="pw 1"
ENV
run() { S=$S PATH="$t/bin:$PATH" HOME="$t/home" MAC_SIGNING_ENV="${ENVF:-$t/env}" sh "$here/ensure-build-keychain.sh" 2>&1; }
want_list="$LOGIN
$KC"

# 1. A bare Mac: no keychain, a login-only list.
echo "$LOGIN" > "$S/list"
out=$(run); rc=$?
[ "$rc" -eq 0 ] && ok "first run exits 0" || bad "first run exits $rc: $out"
[ -f "$KC" ] && ok "keychain created" || bad "keychain not created"
[ "$(cat "$S/unlocked")" = "pw 1" ] && ok "unlocked with the env password" || bad "not unlocked with the env password"
[ "$(cat "$S/list")" = "$want_list" ] && ok "listed after login, path with a space intact" || bad "list is: $(cat "$S/list")"
case "$out" in *CHANGED*created*) ok "reports the creation" ;; *) bad "no creation line: $out" ;; esac
case "$out" in *CHANGED*search*) ok "reports the listing" ;; *) bad "no listing line: $out" ;; esac

# 2. A second run changes nothing.
out=$(run)
[ "$out" = OK ] && ok "second run reports OK" || bad "second run: $out"

# 3. A reset drops it from the list and relocks it: the next run puts it back and unlocks.
echo "$LOGIN" > "$S/list"; rm "$S/unlocked"
out=$(run)
[ "$(cat "$S/list")" = "$want_list" ] && ok "relisted after a reset" || bad "not relisted: $(cat "$S/list")"
[ -f "$S/unlocked" ] && ok "unlocked again" || bad "not unlocked again"
case "$out" in *created*) bad "recreated an existing keychain" ;; *) ok "existing keychain not recreated" ;; esac

# 4. No env file fails loudly.
out=$(ENVF="$t/none" run); rc=$?
[ "$rc" -ne 0 ] && ok "missing env file fails" || bad "missing env file passed"

# 5. A password with shell characters in single quotes reaches unlock unchanged.
printf "MAC_SIGN_KEYCHAIN='%s'\nMAC_SIGN_KEYCHAIN_PASSWORD='a\$b\`c\"d'\n" "$KC" > "$t/env2"
ENVF="$t/env2" run >/dev/null
[ "$(cat "$S/unlocked")" = 'a$b`c"d' ] && ok "special-character password intact" || bad "password mangled: $(cat "$S/unlocked")"

# 6. A path without .keychain-db, a missing password and an empty list each fail and change nothing.
printf 'MAC_SIGN_KEYCHAIN="%s/x.keychain"\nMAC_SIGN_KEYCHAIN_PASSWORD=p\n' "$t" > "$t/env3"
out=$(ENVF="$t/env3" run); rc=$?
[ "$rc" -ne 0 ] && ok "path without -db refused" || bad "path without -db passed"
printf 'MAC_SIGN_KEYCHAIN="%s"\n' "$KC" > "$t/env4"
out=$(ENVF="$t/env4" run); rc=$?
[ "$rc" -ne 0 ] && ok "missing password refused" || bad "missing password passed"
: > "$S/list"
out=$(run); rc=$?
[ "$rc" -ne 0 ] && [ ! -s "$S/list" ] && ok "empty list not rewritten" || bad "empty list: rc=$rc list=$(cat "$S/list")"

[ "$fail" -eq 0 ] && echo "all passed"
exit "$fail"
