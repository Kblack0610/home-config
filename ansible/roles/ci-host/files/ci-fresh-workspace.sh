#!/usr/bin/env bash
# Managed by ansible (ci-host), installed on PATH as ci-fresh-workspace. A job that wants a
# fresh workspace runs it as its first step, before checkout: a light or heavy job of a Unity
# repo, whose default `clean: true` or sparse checkout would wipe or break a warm Library on a
# Unity slot. Tests: test-job-started.sh.
#
#   ci-fresh-workspace            In a workspace holding a Unity project, move the tree to
#                                 $HOME/_warm/<repo>/<repo> (a rename on the same filesystem) and
#                                 leave an empty workspace; in any other, empty it.
#   ci-fresh-workspace --restore  The job hook's half, at job end and job start: if a tree is
#                                 parked for this workspace, the workspace is a light job's, so
#                                 delete it and move the parked tree back. Job start runs it too,
#                                 which catches up a job whose end never ran.
#
# GITHUB_WORKSPACE outside $HOME/_work is left alone, so on a GitHub-hosted or other runner the
# step is a no-op.
set -euo pipefail

ws="${GITHUB_WORKSPACE:-}"
case "$ws" in
  "${HOME:-/nonexistent}"/_work/?*) ;;
  *)
    echo "ci-fresh-workspace: GITHUB_WORKSPACE='${ws}' is outside this slot's HOME (${HOME:-unset}); not touching it" >&2
    exit 0
    ;;
esac
warm="$HOME/_warm/${ws#"$HOME"/_work/}"

if [ "${1:-}" = --restore ]; then
  [ -d "$warm" ] || exit 0
  rm -rf -- "$ws"
  mkdir -p -- "${ws%/*}"
  mv -- "$warm" "$ws"
  echo "ci-fresh-workspace: moved the Unity tree parked in ${warm} back to ${ws}"
  exit 0
fi

mkdir -p -- "$ws"
# Already parked (a second call in one job): the workspace is this job's, whatever it holds.
if [ ! -e "$warm" ] && [ -n "$(find "$ws" -maxdepth 4 -path '*/ProjectSettings/ProjectVersion.txt' -print -quit)" ]; then
  mkdir -p -- "${warm%/*}"
  mv -- "$ws" "$warm"
  mkdir -- "$ws"
  echo "ci-fresh-workspace: parked the Unity tree in ${warm}; the job hook moves it back at job end"
  exit 0
fi
find "$ws" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
echo "ci-fresh-workspace: emptied ${ws}"
