#!/usr/bin/env bash
# Remove Unity CI pool slots from the host this runs on. The inverse of the
# "Unity CI runner pool" play (roles unity-ci-host + github-actions-runner-linux),
# which has no removal path of its own.
#
#   bash scripts/unity-ci-runner-remove.sh            # every slot on this host
#   bash scripts/unity-ci-runner-remove.sh --slot 2   # just slot 2
#   bash scripts/unity-ci-runner-remove.sh --host asus-laptop --slot 2
#
# First take the slots out of unity_ci_runners in ansible/inventory.yml (and
# RUNNER_ROSTER in apps/github-runner-exporter/deployment.yaml), or the next
# playbook run puts them back. Run as yourself, not root: it asks for sudo once,
# and deregisters with your gh (admin:org). Safe to run again. What it removes and
# keeps: ansible/roles/github-actions-runner-linux/README.md, "Uninstall".
set -u
ORG=BlackNBrownStudios
RUNNER_HOME=/var/lib/actions-runner
HOST=$(hostname -s)
SLOTS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST=$2; shift 2 ;;
    --slot) SLOTS+=("$2"); shift 2 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
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

sudo -v || exit 1

for n in "${SLOTS[@]}"; do
  name="$HOST-unity-pool-$n"
  unit="actions.runner.$name.service"
  echo "== slot $n ($name)"
  # Stops the listener; its job's container (docker owns it) is stopped below.
  sudo systemctl disable --now "$unit" 2>/dev/null || true
  sudo rm -f "/etc/systemd/system/$unit"
  sudo rm -rf "$RUNNER_HOME/unity-pool-$n"
  id=""
  if command -v gh >/dev/null; then
    id=$(gh api "orgs/$ORG/actions/runners" --jq ".runners[]|select(.name==\"$name\").id" 2>/dev/null)
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
  docker ps -q --filter name=unity-test | xargs -r docker stop
  sudo rm -f /etc/systemd/system/unity-ci.slice
  sudo systemctl stop unity-ci.slice 2>/dev/null || true
  sudo rm -rf "$RUNNER_HOME/asset-library" "$RUNNER_HOME/unity-machine-id"
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
systemctl list-units --all "actions.runner.$HOST-unity-pool-*" --no-legend
if command -v gh >/dev/null; then
  echo "== check: org runners still named $HOST-unity-pool-* (none listed = clean)"
  gh api "orgs/$ORG/actions/runners" --jq '.runners[].name' 2>/dev/null | grep -- "^$HOST-unity-pool-" || true
fi
echo "== done"
