#!/usr/bin/env bash
# Managed by ansible (ci-host), on PATH as ci-fresh-workspace: the one owner of what a slot's job
# workspace holds between jobs (docs/runbooks/unity-ci-host.md, Disk). Tests: test-job-started.sh.
#
#   ci-fresh-workspace             A light job's first step, before checkout: in a Unity checkout it
#                                  moves every untracked path (Library, Logs, ...) to $HOME/_warm,
#                                  so the job's clean checkout stays incremental and wipes none of it.
#   ci-fresh-workspace --job-hook start|end
#                                  The job hook's pass at job start and end: every parked path goes
#                                  back (a slot runs one job at a time, so each is this job's or a
#                                  dead job's), then a Unity workspace is kept and any other emptied.
#                                  A job start that finds the stamp of a job from an earlier boot (the
#                                  host went down under it) removes that workspace's Unity Libraries:
#                                  a hard reset leaves zero-byte artifacts, and every later job on the
#                                  slot fails on them ("VirtualArtifacts/... is either empty or
#                                  corrupted", tuf-b550 2026-11-20). The next job imports cold.
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
# Each Unity project's ProjectVersion.txt under $1 (the checkout and its release trees); extra
# find arguments go after -print.
unity_projects() { find "$1" -maxdepth 4 -name Library -prune -o -path '*/ProjectSettings/ProjectVersion.txt' -print "${@:2}"; }
unity_project() { [ -n "$(unity_projects "$1" -quit)" ]; }
# One job at a time per slot, so one stamp per slot HOME: "<boot_id> <workspace>", written at job
# start and removed at job end. CI_BOOT_ID_FILE is for the test.
stamp="$HOME/.ci-job-boot"
boot_id=$(cat "${CI_BOOT_ID_FILE:-/proc/sys/kernel/random/boot_id}" 2>/dev/null || true)
# At job start: a stamp from an earlier boot means the host went down under that job.
drop_torn_libraries() {
  local was torn v lib
  [ -f "$stamp" ] || return 0
  read -r was torn < "$stamp" || true
  case "$torn" in "$HOME"/_work/?*) ;; *) return 0 ;; esac
  [ "$was" != "$boot_id" ] && [ -d "$torn" ] || return 0
  unity_projects "$torn" | while IFS= read -r v; do
    lib="${v%/ProjectSettings/ProjectVersion.txt}/Library"
    [ -e "$lib" ] || continue
    # The loop is a subshell, so exit fails the pipeline and, through set -e, the job start.
    rm -rf -- "$lib" || { echo "ci-fresh-workspace: could not remove ${lib}, torn when the host went down during the last job in ${torn}" >&2; exit 1; }
    echo "ci-fresh-workspace: the host went down during the last job in ${torn}; removed ${lib}, which a hard reset leaves torn"
  done
}
# $1 is the hook's event, start or end; any other value leaves the stamp alone. It runs before the
# rest of the hook, so a job end whose cleanup fails has still cleared its stamp.
job_stamp() {
  case "$1" in
    end) rm -f -- "$stamp" ;;
    start)
      if [ -z "$boot_id" ]; then rm -f -- "$stamp"; return 0; fi
      drop_torn_libraries
      printf '%s %s\n' "$boot_id" "$ws" > "$stamp"
      ;;
  esac
}

if [ "${1:-}" = --job-hook ]; then
  job_stamp "${2:-}"
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
