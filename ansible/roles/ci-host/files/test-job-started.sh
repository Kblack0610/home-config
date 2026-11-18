#!/bin/sh
# Offline test of templates/job-started.sh.j2, every slot's job-started hook: a scratch slot
# HOME and a stub prune script. CI runs it in the checks workflow.
set -u
here=$(cd "$(dirname "$0")" && pwd)
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
fail=0
ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }

printf '#!/bin/sh\necho pruned >> %s/pruned; exit "${PRUNE_RC:-0}"\n' "$t" > "$t/prune"
chmod +x "$t/prune"
sed "s|{{ linux_ci_disk_prune }}|$t/prune|" "$here/../templates/job-started.sh.j2" > "$t/hook"
chmod +x "$t/hook"
grep -q '{{' "$t/hook" && { echo "FAIL the template has a variable this test does not fill"; exit 1; }

home="$t/slot"
plat="$home/_work/platform/platform"
game="$home/_work/unity-core-playground/unity-core-playground"
mkdir -p "$plat/node_modules" "$game/ProjectSettings" "$game/Library"
touch "$plat/package.json" "$game/ProjectSettings/ProjectVersion.txt" "$game/Library/warm"
run() { HOME="$home" GITHUB_WORKSPACE="$1" CI_SLOT_UNITY="${2:-}" "$t/hook" >/dev/null 2>&1; }

run "$plat"; ok "a light slot empties a non-Unity workspace" '[ -z "$(ls -A "$plat")" ] && [ ! -e "$t/pruned" ]'
run "$game" 1; ok "a Unity slot keeps a Unity project's Library" '[ -e "$game/Library/warm" ]'
ok "a Unity slot prunes before the job" '[ "$(cat "$t/pruned")" = pruned ]'
touch "$plat/package.json"
PRUNE_RC=3 run "$plat" 1; ok "a failed prune never fails the job, and the workspace is still emptied" '[ $? -eq 0 ] && [ -z "$(ls -A "$plat")" ]'
mkdir -p "$t/elsewhere/x"
run "$t/elsewhere"; ok "a workspace outside the slot's HOME is not touched" '[ -d "$t/elsewhere/x" ]'
run "$home/_work/missing/missing"; ok "a missing workspace is a no-op" '[ $? -eq 0 ]'
exit $fail
