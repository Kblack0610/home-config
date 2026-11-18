#!/usr/bin/env bash
# Managed by ansible (ci-host), on PATH as ci-fresh-workspace: the one owner of what a slot's job
# workspace holds between jobs (docs/runbooks/unity-ci-host.md, Disk). Tests: test-job-started.sh.
#
#   ci-fresh-workspace             A light job's first step, before checkout: in a Unity checkout it
#                                  moves every untracked path (Library, Logs, ...) to $HOME/_warm,
#                                  so the job's clean checkout stays incremental and wipes none of it.
#   ci-fresh-workspace --job-hook  The job hook's pass at job start and end: every parked path goes
#                                  back (a slot runs one job at a time, so each is this job's or a
#                                  dead job's), then a Unity workspace is kept and any other emptied.
set -euo pipefail

ws="${GITHUB_WORKSPACE:-}"
case "$ws" in
  "${HOME:-/nonexistent}"/_work/?*) ;;
  *)
    echo "ci-fresh-workspace: GITHUB_WORKSPACE='${ws}' is outside this slot's HOME (${HOME:-unset}); not touching it" >&2
    exit 0
    ;;
esac
park="$HOME/_warm"
unity_project() { [ -n "$(find "$1" -maxdepth 4 -name Library -prune -o -path '*/ProjectSettings/ProjectVersion.txt' -print -quit)" ]; }

if [ "${1:-}" = --job-hook ]; then
  # <repo>/<repo>.paths lists what was parked, NUL-separated, written before any move.
  for list in "$park"/*/*.paths; do
    [ -f "$list" ] || continue
    tree="${list%.paths}"
    dest="$HOME/_work/${tree#"$park"/}"
    if [ -e "$dest/.git" ]; then
      while IFS= read -r -d '' p; do
        p="${p%/}"
        [ -e "$tree/$p" ] || [ -L "$tree/$p" ] || continue
        rm -rf -- "${dest:?}/$p"
        mkdir -p -- "$(dirname -- "$dest/$p")"
        mv -- "$tree/$p" "$dest/$p"
      done < "$list"
      echo "ci-fresh-workspace: moved the untracked state parked in ${tree} back to ${dest}"
    else
      echo "ci-fresh-workspace: ${dest} is no longer a checkout; dropped the state parked in ${tree}"
    fi
    rm -rf -- "$tree" "$list" "$list.new"
  done
  rm -rf -- "$HOME/setup-pnpm"
  [ -d "$ws" ] || exit 0
  if unity_project "$ws"; then
    echo "ci-fresh-workspace: ${ws} holds a Unity project; kept"
    exit 0
  fi
  find "$ws" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  echo "ci-fresh-workspace: emptied ${ws}"
  exit 0
fi

# Nothing to park elsewhere: the job hook emptied a non-Unity workspace at job start, and a checkout
# replaces a tree with no .git.
[ -e "$ws/.git" ] && unity_project "$ws" || exit 0
tree="$park/${ws#"$HOME"/_work/}"
list="$tree.paths"
if [ -z "${ACTIONS_RUNNER_HOOK_JOB_COMPLETED:-}" ]; then
  echo "ci-fresh-workspace: ${ws} holds a Unity project, and this slot has no job-end hook to put its untracked state back; apply roles/ci-host (--tags unity-ci) to the slot" >&2
  exit 1
fi
mkdir -p -- "$tree"
# The whole list before any move, so a later call (a retry, or a second call in the job) finishes
# a failed one; a listed path parked already and re-created since is the job's own, and stays.
if [ ! -e "$list" ]; then
  git -c safe.directory="$ws" -C "$ws" ls-files -z --others --directory > "$list.new"
  mv -- "$list.new" "$list"
fi
while IFS= read -r -d '' p; do
  p="${p%/}"
  if { [ -e "$ws/$p" ] || [ -L "$ws/$p" ]; } && ! { [ -e "$tree/$p" ] || [ -L "$tree/$p" ]; }; then
    mkdir -p -- "$(dirname -- "$tree/$p")"
    mv -- "$ws/$p" "$tree/$p"
  fi
done < "$list"
echo "ci-fresh-workspace: parked the untracked state of ${ws} in ${tree}; the job hook moves it back"
