#!/usr/bin/env bash
# Managed by ansible (ci-host), on PATH as ci-fresh-workspace: the one owner of what a slot's job
# workspace holds between jobs (docs/runbooks/unity-ci-host.md, Disk). Tests: test-job-started.sh.
#
#   ci-fresh-workspace             A job's own first step, before checkout, when it wants a fresh
#                                  workspace: a Unity tree moves to $HOME/_warm/<repo>/<repo>, any
#                                  other workspace is emptied.
#   ci-fresh-workspace --job-hook  The job hook's pass at job start and end: every parked tree goes
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
warm="$park/${ws#"$HOME"/_work/}"
unity_project() { [ -n "$(find "$1" -maxdepth 4 -path '*/ProjectSettings/ProjectVersion.txt' -print -quit)" ]; }
empty() { find "$1" -mindepth 1 -maxdepth 1 -exec rm -rf {} +; }

if [ "${1:-}" = --job-hook ]; then
  for tree in "$park"/*/*; do
    [ -d "$tree" ] || continue
    dest="$HOME/_work/${tree#"$park"/}"
    rm -rf -- "$dest"
    mkdir -p -- "${dest%/*}"
    mv -- "$tree" "$dest"
    echo "ci-fresh-workspace: moved the Unity tree parked in ${tree} back to ${dest}"
  done
  rm -rf -- "$HOME/setup-pnpm"
  [ -d "$ws" ] || exit 0
  if unity_project "$ws"; then
    echo "ci-fresh-workspace: ${ws} holds a Unity project; kept"
    exit 0
  fi
  empty "$ws"
  echo "ci-fresh-workspace: emptied ${ws}"
  exit 0
fi

mkdir -p -- "$ws"
# A parked tree already (a second call in one job): the workspace is this job's, whatever it holds.
if [ ! -e "$warm" ] && unity_project "$ws"; then
  if [ -z "${ACTIONS_RUNNER_HOOK_JOB_COMPLETED:-}" ]; then
    echo "ci-fresh-workspace: ${ws} holds a Unity project, and this slot has no job-end hook to put it back; apply roles/ci-host (--tags unity-ci) to the slot" >&2
    exit 1
  fi
  mkdir -p -- "${warm%/*}"
  mv -- "$ws" "$warm"
  mkdir -- "$ws"
  echo "ci-fresh-workspace: parked the Unity tree in ${warm}; the job hook moves it back"
  exit 0
fi
empty "$ws"
echo "ci-fresh-workspace: emptied ${ws}"
