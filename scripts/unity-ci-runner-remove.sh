#!/usr/bin/env bash
# Remove Unity CI pool slots from the host this runs on. The inverse of the
# "Unity CI runner pool" play (roles unity-ci-host + github-actions-runner-linux),
# which has no removal path of its own.
#
#   bash scripts/unity-ci-runner-remove.sh            # every slot on this host
#   bash scripts/unity-ci-runner-remove.sh --slot 2   # just slot 2
#   bash scripts/unity-ci-runner-remove.sh --host asus-laptop --slot 2
#   ... --force                                        # even a slot mid-job
#
# First take the slots out of unity_ci_runners in ansible/inventory.yml (and
# RUNNER_ROSTER in apps/github-runner-exporter/deployment.yaml), or the next
# playbook run puts them back. Run as yourself, not root: it asks for sudo once,
# and deregisters with your gh (admin:org). Safe to run again. What it removes and
# keeps: ansible/roles/github-actions-runner-linux/README.md, "Uninstall".
set -u
ORG=BlackNBrownStudios
RUNNER_HOME=/var/lib/actions-runner
HOST=${HOSTNAME%%.*}  # bash's own: hp-victus has no hostname binary
SLOTS=()
FORCE=0
SKIPPED=()

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST=$2; shift 2 ;;
    --slot) SLOTS+=("$2"); shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ ${#SLOTS[@]} -eq 0 ]; then
  for f in /etc/systemd/system/actions.runner."$HOST"-unity-pool-*.service; do
    [ -e "$f" ] || continue
    n=${f##*-unity-pool-}; SLOTS+=("${n%.service}")
  done
fi
if [ ${#SLOTS[@]} -eq 0 ]; then
  echo "no Unity pool slots for $HOST under /etc/systemd/system (wrong --host?)"
fi

# sudo -v asks for a password even under NOPASSWD (sudoers verifypw), so try -n first.
sudo -n true 2>/dev/null || sudo -v || exit 1

for n in "${SLOTS[@]}"; do
  name="$HOST-unity-pool-$n"
  unit="actions.runner.$name.service"
  echo "== slot $n ($name)"
  # A Runner.Worker is a job in flight; stopping the unit cancels it. Without this
  # a slot that took a job between the idle check and the run lost a Slim tour
  # job on razer (2026-11-10).
  if [ "$FORCE" = 0 ] && pgrep -f "$RUNNER_HOME/unity-pool-$n/bin[^ ]*/Runner.Worker" >/dev/null; then
    echo "  running a job: skipped. Run again when it finishes, or pass --force."
    SKIPPED+=("$n")
    continue
  fi
  # Stops the listener; its job's container (docker owns it) is stopped below.
  sudo systemctl disable --now "$unit" 2>/dev/null || true
  sudo rm -f "/etc/systemd/system/$unit"
  sudo rm -rf "$RUNNER_HOME/unity-pool-$n"
  id=""
  if command -v gh >/dev/null; then
    # On a 403 gh prints the error body to stdout, so keep it only on success.
    id=$(gh api "orgs/$ORG/actions/runners" --paginate --jq ".runners[]|select(.name==\"$name\").id" 2>/dev/null) || id=""
  fi
  if [ -n "$id" ]; then
    gh api -X DELETE "orgs/$ORG/actions/runners/$id" && echo "deregistered $name"
  else
    echo "not deregistered here (gone already, or no gh with admin:org on this host); from the workstation:"
    echo "  gh api -X DELETE orgs/$ORG/actions/runners/\$(gh api orgs/$ORG/actions/runners --jq '.runners[]|select(.name==\"$name\").id')"
  fi
done

# Only when no Unity slot is left: the host-wide pieces unity-ci-host installed.
if ! ls /etc/systemd/system/actions.runner.*-unity-pool-*.service >/dev/null 2>&1; then
  echo "== no Unity slots left: removing the host-wide pieces"
  sudo docker ps -q --filter name=unity-test | xargs -r sudo docker stop
  # Every unity_ci_asset_libraries copy (asset-library, asset-library-hd). The glob
  # runs as root: $RUNNER_HOME is not readable as yourself.
  sudo sh -c 'rm -rf "$1"/asset-library* "$1/unity-machine-id"' _ "$RUNNER_HOME"
  if ! ls /etc/systemd/system/actions.runner.*.service >/dev/null 2>&1; then
    echo "== no runner of any kind left: removing the actions-runner user"
    sudo pkill -u actions-runner || true
    if getent passwd actions-runner >/dev/null; then sudo userdel actions-runner; fi
    sudo rm -rf "$RUNNER_HOME"
  else
    echo "other runners still use actions-runner; keeping the user and $RUNNER_HOME"
  fi
fi
sudo systemctl daemon-reload
sudo systemctl reset-failed

echo "== check: the Unity units left on $HOST (none listed = clean)"
systemctl list-units --all "actions.runner.$HOST-unity-pool-*" --no-legend | grep -v not-found
if command -v gh >/dev/null; then
  echo "== check: org runners still named $HOST-unity-pool-* (none listed = clean)"
  if names=$(gh api "orgs/$ORG/actions/runners" --paginate --jq '.runners[].name' 2>/dev/null); then
    grep -- "^$HOST-unity-pool-" <<<"$names" || true
  else
    echo "gh here cannot list org runners (no admin:org); check from the workstation"
  fi
fi
if [ ${#SKIPPED[@]} -gt 0 ]; then
  echo "== not removed (mid-job): slot ${SKIPPED[*]}"
  exit 1
fi
echo "== done"
