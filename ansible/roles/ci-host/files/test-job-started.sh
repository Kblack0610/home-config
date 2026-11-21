#!/bin/sh
# Offline test of templates/job-started.sh.j2, every slot's job start and end hook, and of
# ci-fresh-workspace.sh, the light job's half of it: a scratch slot HOME and a stub prune
# script. CI runs it in the checks workflow.
set -u
here=$(cd "$(dirname "$0")" && pwd)
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
fail=0
ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }

printf '#!/bin/sh\necho pruned >> %s/pruned; exit "${PRUNE_RC:-0}"\n' "$t" > "$t/prune"
chmod +x "$t/prune"
cp "$here/ci-fresh-workspace.sh" "$t/fresh"; chmod +x "$t/fresh"
sed -e "s|{{ linux_ci_disk_prune }}|$t/prune|" -e "s|{{ linux_ci_fresh_workspace }}|$t/fresh|" \
  "$here/../templates/job-started.sh.j2" > "$t/hook"
chmod +x "$t/hook"
ln -s hook "$t/job-completed.sh"
grep -q '{{' "$t/hook" && { echo "FAIL the template has a variable this test does not fill"; exit 1; }

home="$t/slot"
echo boot-a > "$t/boot"; export CI_BOOT_ID_FILE="$t/boot"
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

# The boot stamp: a job the host went down under leaves its Unity Libraries torn.
lib() { mkdir -p "$game/Library" "$game/Builds/release-tree/ProjectSettings" "$game/Builds/release-tree/Library"
  touch "$game/Library/warm" "$game/Builds/release-tree/ProjectSettings/ProjectVersion.txt" "$game/Builds/release-tree/Library/warm"; }
lib; end "$game"; run "$game" 1
ok "a job start stamps the slot with the boot and the workspace" '[ "$(cat "$home/.ci-job-boot")" = "boot-a $game" ]'
end "$game"; ok "job end removes the stamp" '[ ! -e "$home/.ci-job-boot" ]'
run "$game" 1; echo boot-b > "$t/boot"; mkdir -p "$plat/node_modules"
run "$plat"; ok "a job start after the host went down mid-job removes that workspace's Libraries, release trees too" '[ ! -e "$game/Library" ] && [ ! -e "$game/Builds/release-tree/Library" ] && [ -e "$game/ProjectSettings/ProjectVersion.txt" ]'
ok "and stamps the new job" '[ "$(cat "$home/.ci-job-boot")" = "boot-b $plat" ]'
lib; run "$game" 1; run "$game" 1; ok "a job whose end never ran, on the same boot, keeps the Library" '[ -e "$game/Library/warm" ]'
end "$game"; echo boot-c > "$t/boot"; run "$game" 1
ok "a reboot between jobs keeps the Library" '[ -e "$game/Library/warm" ]'
mkdir -p "$t/elsewhere/Library"; echo "boot-a $t/elsewhere" > "$home/.ci-job-boot"; run "$game" 1
ok "a stamp naming a path outside the slot's HOME is ignored" '[ -d "$t/elsewhere/Library" ] && [ -e "$game/Library/warm" ]'
end "$game"; echo boot-a > "$t/boot"

# A light job on a Unity slot: ci-fresh-workspace moves the checkout's untracked state (Library and
# the rest) aside, the job's clean checkout runs on the tracked tree, and the job-end hook puts the
# state back. Run from inside the workspace, as a job step is.
fresh() { (cd "$1" && HOME="$home" GITHUB_WORKSPACE="$1" ACTIONS_RUNNER_HOOK_JOB_COMPLETED="$t/job-completed.sh" "$t/fresh" >/dev/null 2>&1); }
tree="$home/_warm/unity-core-playground/unity-core-playground"
checkout() {
  rm -rf "$game"; mkdir -p "$game/ProjectSettings" "$game/Assets" "$game/Packages"
  touch "$game/ProjectSettings/ProjectVersion.txt" "$game/Assets/a.txt" "$game/Packages/manifest.json"
  git -C "$game" init -q && git -C "$game" add -A && git -C "$game" -c user.email=t@t -c user.name=t commit -qm t
  mkdir -p "$game/Library" "$game/Logs" "$game/Assets/StreamingAssets"
  touch "$game/Library/warm" "$game/Logs/a b" "$game/Packages/packages-lock.json" "$game/Assets/x y.meta" "$game/Assets/StreamingAssets/s"
}
parked() { [ ! -e "$game/Library" ] && [ ! -e "$game/Logs" ] && [ ! -e "$game/Packages/packages-lock.json" ] && [ ! -e "$game/Assets/x y.meta" ] && [ -e "$tree/Library/warm" ]; }
back() { [ -e "$game/Library/warm" ] && [ -e "$game/Logs/a b" ] && [ -e "$game/Packages/packages-lock.json" ] && [ -e "$game/Assets/x y.meta" ] && [ -e "$game/Assets/StreamingAssets/s" ] && [ ! -e "$tree" ] && [ ! -e "$tree.paths" ]; }
checkout; run "$game" 1; fresh "$game"
ok "a light job's first step parks every untracked path and keeps the tracked tree" 'parked && [ -e "$game/Assets/a.txt" ] && [ -d "$game/.git" ]'
git -C "$game" clean -qffdx; mkdir -p "$game/Library"; touch "$game/Library/light" "$game/light-out"
fresh "$game"
ok "a second call in the job leaves the job's own files and the parked state" '[ -e "$game/light-out" ] && [ -e "$game/Library/light" ] && [ -e "$tree/Library/warm" ]'
end "$game"; ok "job end moves every parked path back over the job's own" 'back && [ ! -e "$game/Library/light" ]'
fresh "$game"; run "$game" 1; ok "a job start after a job whose end never ran moves it back" 'back'
fresh "$game"; rm -rf "$game/Packages"
end "$game"; ok "a parked path goes back when the job removed its tracked parent (a sparse checkout)" 'back'
fresh "$game"; rm -rf "$game"
run "$game" 1; ok "state parked for a checkout that is gone is dropped" '[ ! -e "$tree" ] && [ ! -e "$tree.paths" ] && [ ! -e "$game" ]'
checkout; fresh "$game"; mkdir -p "$plat/node_modules"
run "$plat"; ok "another repo's job start puts back what a dead light job parked" 'back && [ -z "$(ls -A "$plat")" ]'
mkdir -p "$plat/node_modules"; fresh "$plat"
ok "in a non-Unity workspace it does nothing (the job hook emptied it)" '[ -d "$plat/node_modules" ] && [ ! -e "$home/_warm/platform" ]'
rm -rf "$game/.git"; fresh "$game"; ok "a Unity tree that is not a checkout is left alone" '[ -e "$game/Library/warm" ] && [ ! -e "$tree" ]'
fresh "$t/elsewhere"; ok "a workspace outside the slot's HOME is not touched by it" '[ -d "$t/elsewhere/x" ]'
HOME="$home" GITHUB_WORKSPACE="$home/_work/new/new" "$t/fresh" >/dev/null 2>&1
ok "a missing workspace is a no-op" '[ $? -eq 0 ] && [ ! -e "$home/_work/new" ]'
checkout; end "$game"; ok "with nothing parked, job end keeps a Unity project as before" '[ -e "$game/Library/warm" ]'
(cd "$game" && HOME="$home" GITHUB_WORKSPACE="$game" "$t/fresh" >/dev/null 2>&1)
ok "a slot with no job-end hook fails the step and parks nothing" '[ $? -ne 0 ] && [ -e "$game/Library/warm" ] && [ ! -e "$tree" ]'
# An mv that fails on one path, standing in for a full disk or a file the slot cannot move.
real_mv=$(command -v mv)
printf '#!/bin/sh\ncase "$*" in *Logs*) exit 1;; esac\nexec %s "$@"\n' "$real_mv" > "$t/bin/mv"; chmod +x "$t/bin/mv"
checkout; (cd "$game" && PATH="$t/bin:$PATH" HOME="$home" GITHUB_WORKSPACE="$game" ACTIONS_RUNNER_HOOK_JOB_COMPLETED="$t/job-completed.sh" "$t/fresh" >/dev/null 2>&1)
ok "a park that fails part way fails the step" '[ $? -ne 0 ] && [ -e "$game/Logs/a b" ]'
fresh "$game"; ok "the next call finishes it" 'parked'
end "$game"; ok "and job end puts all of it back" 'back'
game2="$home/_work/other-game/other-game"; tree2="$home/_warm/other-game/other-game"
mkdir -p "$game2/ProjectSettings" "$game2/Library"; touch "$game2/ProjectSettings/ProjectVersion.txt" "$game2/Library/warm2"
git -C "$game2" init -q && git -C "$game2" add ProjectSettings && git -C "$game2" -c user.email=t@t -c user.name=t commit -qm t
fresh "$game2"; fresh "$game"
run "$plat"; ok "one job start puts back two repos' parked state" 'back && [ -e "$game2/Library/warm2" ] && [ ! -e "$tree2" ]'
exit $fail
