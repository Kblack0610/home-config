#!/usr/bin/env bash
# dhcp.sh sync against a fake router: two adjacent removals, an update after
# them and an add must leave exactly devices.yaml on the router (hc-235).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mkdir "$work/bin"
cp "$here/../dhcp.sh" "$here/devices.yaml" "$work/"
cp "$here/router.uci-show" "$work/state"
ln -s "$here/fake-uci" "$work/bin/uci"
ln -s "$here/fake-ssh" "$work/bin/ssh"
export PATH="$work/bin:$PATH" FAKE_UCI_STATE="$work/state"

"$work/dhcp.sh" sync --force >/dev/null 2>&1

hosts="$(uci -X show dhcp | awk -F"'" '/\.name=/{n=$2} /\.ip=/{print n, $2}' | sort)"
want="$(printf '%s\n' 'keep-a 192.168.1.4' 'keep-b 192.168.1.41' 'new-c 192.168.1.42' 'pc-mac-m1 192.168.1.6')"
fail=0
if [[ "$hosts" != "$want" ]]; then
    echo "FAIL: router hosts after sync"; diff <(echo "$want") <(echo "$hosts") || true; fail=1
fi
if ! diff_out="$("$work/dhcp.sh" diff 2>&1)" || ! grep -q 'Already in sync' <<<"$diff_out"; then
    echo "FAIL: diff after sync is not clean"; echo "$diff_out" | tail -n 20; fail=1
fi
[[ "$fail" -eq 0 ]] && echo "ok: two adjacent removals, one update and one add sync clean"
exit "$fail"
