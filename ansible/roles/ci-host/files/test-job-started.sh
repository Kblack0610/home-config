#!/bin/sh
# Offline test of templates/job-started.sh.j2, every slot's job start and end hook: a scratch slot
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
ln -s hook "$t/job-completed.sh"
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
mkdir -p "$home/setup-pnpm/node_modules/.bin/store" "$home/.cache/ms-playwright"
touch "$home/setup-pnpm/node_modules/.bin/store/blob" "$home/.cache/ms-playwright/chromium"
run "$game" 1; ok "setup-pnpm goes, even beside a kept Unity workspace" '[ ! -e "$home/setup-pnpm" ] && [ -e "$game/Library/warm" ]'
ok "the Playwright cache stays" '[ -e "$home/.cache/ms-playwright/chromium" ]'
mkdir -p "$t/elsewhere/x" "$t/elsewhere-home/setup-pnpm"
HOME="$t/elsewhere-home" GITHUB_WORKSPACE="$t/elsewhere" "$t/hook" >/dev/null 2>&1
ok "a workspace outside HOME leaves that HOME's setup-pnpm" '[ -d "$t/elsewhere-home/setup-pnpm" ]'
run "$t/elsewhere"; ok "a workspace outside the slot's HOME is not touched" '[ -d "$t/elsewhere/x" ]'
run "$home/_work/missing/missing"; ok "a missing workspace is a no-op" '[ $? -eq 0 ]'

# The job-end pass: the job-completed.sh name, as the role links it.
end() { HOME="$home" GITHUB_WORKSPACE="$1" CI_SLOT_UNITY=1 "$t/job-completed.sh" >/dev/null 2>&1; }
rm -f "$t/pruned"; mkdir -p "$plat/node_modules" "$home/setup-pnpm/store"
end "$plat"; ok "the job-end pass empties the workspace and setup-pnpm, and never prunes" '[ -z "$(ls -A "$plat")" ] && [ ! -e "$home/setup-pnpm" ] && [ ! -e "$t/pruned" ]'
end "$game"; ok "the job-end pass keeps a Unity project's Library" '[ -e "$game/Library/warm" ]'
# An rm that fails on one path, standing in for a root-owned file (CI runs this as root).
mkdir -p "$t/bin" "$plat/stuck"; real_rm=$(command -v rm)
printf '#!/bin/sh\ncase "$*" in *stuck*) exit 1;; esac\nexec %s "$@"\n' "$real_rm" > "$t/bin/rm"; chmod +x "$t/bin/rm"
PATH="$t/bin:$PATH" end "$plat"; ok "a job-end cleanup that fails never fails the job" '[ $? -eq 0 ]'
PATH="$t/bin:$PATH" run "$plat"; ok "the same failure at job start fails the job" '[ $? -ne 0 ]'
exit $fail
