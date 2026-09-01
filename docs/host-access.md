# Host access: reaching nodes when SSH is broken

Sooner or later you'll need to touch a node that won't take your SSH key. PXE seeded one key at install time; if your workstation key has rotated since, or the node predates the current kickstart key, ordinary SSH is dead in the water. This doc covers the **escape hatch we actually use** (`kubectl debug node/...`), the naming traps that bite first, and the recipe to re-bootstrap SSH.

See also: `CLAUDE.md#host-access-naming-gotcha`, `docs/architecture.md`, `infrastructure/pxe-server/http/kickstart/disk-install.sh:456-466` (where PXE seeds the key).

## Naming: four systems, one box

Every bare-metal Pi has **four different names** for the same physical machine, each resolved by a different system. Get them confused and you'll send commands to nothing:

| Where | Name shape | Example | Resolved by | Notes |
|-------|-----------|---------|-------------|-------|
| `ansible/inventory.yml` `--limit` | bare alias | `pi5-master`, `pi5-worker2`, `pi4-worker4` | Ansible only | NOT DNS-resolvable |
| `ssh`, `kubectl`, `curl`, `nc` from your shell | DHCP name | `pi5-master-lan`, `pi5-worker2-lan` | OpenWRT dnsmasq via `infrastructure/dhcp/devices.yaml` | `-lan` suffix is convention here |
| `kubectl get nodes`, `kubectl debug node/...` | k8s node name | `raspberrypi`, `raspberrypi-e3a771f1` | OS hostname (Pis kept default `raspberrypi[-suffix]`) | Random suffix per host, ugly but stable |
| Always works | IP | `192.168.1.20` (master), `.21–.24, .124` (workers) | — | The unambiguous bottom layer |

Mapping for the current fleet (regenerate any time with `kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.addresses[?(@.type=="InternalIP")].address}{"\n"}{end}'`):

| Ansible alias | DHCP name | k8s node name | IP |
|---|---|---|---|
| `pi5-master` | `pi5-master-lan` | `raspberrypi` | `192.168.1.20` |
| `pi5-worker1` | `pi5-worker1-lan` | `raspberrypi-23a7710c` | `192.168.1.21` |
| `pi5-worker2` | `pi5-worker2-lan` | `raspberrypi-e3a771f1` | `192.168.1.22` |
| `pi5-worker3` | `pi5-worker3-lan` | `raspberrypi-771be84c` | `192.168.1.23` |
| `pi4-worker4` | `pi4-worker4-lan` | `raspberrypi-7386c525` | `192.168.1.24` |
| `pi4-worker5` | `pi4-worker5-lan` | `raspberrypi-b814834e` | `192.168.1.124` |
| `pi3-adguard` | `pi3-adguard-lan` | n/a (not a k3s node) | `192.168.1.193` |

pi3 is the odd one out: it is not in the cluster, so there is no k8s node name and
`kubectl debug` is not available as an escape hatch. It also kept a real hostname
(`pi3-adguard`) instead of the default `raspberrypi`, and `ssh pi3` works via
`~/.ssh/config`. It runs AdGuard Home, which is the LAN's only DNS resolver, so
when it is down name resolution fails for everything - including whatever you
were about to use to debug it. Reach it by IP.

Macs use `~/.ssh/config` aliases (`m1`, `mac-studio`) — see `docs/mac-machines.md`. The `-lan` suffix is a Pi/x86 convention only.

## Quick decision tree

```
Need to touch a node:
├── Can you SSH? (`ssh <ip>` works)
│   ├── yes → ansible / ssh / scp — normal life
│   └── no  → SSH key probably mismatched. See "Recipe: re-bootstrap SSH" below.
└── Is the node up but `kubectl get node <k8s-name>` shows NotReady?
    └── If k3s is sick, kubectl debug won't work either. Last resort: console / IPMI / unplug.
```

If SSH is dead but the kubelet is alive, **`kubectl debug node/...` is the escape hatch**. It schedules a privileged pod on the node with the host filesystem mounted at `/host`, no network access to the workstation needed beyond the k8s API.

## The escape hatch: `kubectl debug node/<k8s-name>`

```bash
kubectl --context home-k3s debug node/<k8s-name> -it \
  --image=alpine:latest \
  --profile=sysadmin \
  -- chroot /host sh
```

What that gets you:

- A throwaway pod (`node-debugger-<k8s-name>-XXXXX` in the `default` namespace) scheduled directly on the target node.
- `--profile=sysadmin` gives it the capabilities it needs to actually read/write host state (without it, you get a CrashLoopBackOff from missing perms).
- `chroot /host sh` drops you into the host's root filesystem with full sudo-equivalent rights. From there, edit files, restart services, anything.

Important gotchas:

- **Use the k8s name, not the Ansible alias or DHCP name.** `kubectl debug node/pi5-worker2` fails with "node not found"; `kubectl debug node/raspberrypi-e3a771f1` works.
- **Debug pods aren't auto-cleaned** when `kubectl debug` exits. They show up as `Completed` in `kubectl get pods -n default`. Clean up with `kubectl --context home-k3s delete pods -n default -l '!app' --field-selector status.phase=Succeeded` after a session.
- **Don't trust stdout from non-interactive runs against multiple nodes.** Looping `for node in ...; kubectl debug ...` sometimes swallows output for the 3rd+ iteration. Verify the work via the pod logs (`kubectl logs node-debugger-<name>-XXXXX`) instead of trusting the inline output.
- **The `default` namespace is where the debug pod lands** unless you pass `-n <ns>`. Watch out for namespace quotas.

## Recipe: re-bootstrap SSH on a host you can't SSH into

The most common reason SSH dies on these nodes: the workstation's key has changed but the node's `authorized_keys` still holds an old one (often because the node was provisioned by an older PXE kickstart with a different seeded key). Fix:

```bash
# 1. Get the workstation's current public key
WS_KEY=$(cat ~/.ssh/id_ed25519.pub)

# 2. Pick the target — use the k8s node name from `kubectl get nodes`
K8S_NAME="raspberrypi-e3a771f1"   # e.g. pi5-worker2

# 3. Append the key via a debug pod (idempotent — checks before appending)
kubectl --context home-k3s debug "node/${K8S_NAME}" --image=alpine:latest --profile=sysadmin \
  -- chroot /host sh -c "
    F=/home/kblack0610/.ssh/authorized_keys
    if grep -qF '${WS_KEY}' \$F 2>/dev/null; then
      echo 'already present'
    else
      echo '${WS_KEY}' >> \$F
      chown kblack0610:kblack0610 \$F
      chmod 600 \$F
      echo 'appended'
    fi
  "

# 4. Test
ssh -o BatchMode=yes <node-ip> 'hostname'
```

A few notes on the recipe:

- The user is `kblack0610` because that's what PXE creates (`infrastructure/pxe-server/http/kickstart/disk-install.sh`). If a host has a different user, adjust the path.
- `grep -qF` is exact-string match so re-running is safe.
- The append-don't-replace pattern preserves existing keys (other workstations, the PXE-seeded key, etc.). If you want to clean out stale keys, edit the file by hand inside the chroot — but err on the side of leaving them; an extra key in `authorized_keys` is harmless, a missing one locks you out.
- After the first node, `ssh-keygen -R <ip>` if you get a host-key mismatch warning (likely if the node was reimaged at any point).

## Bulk version: fix all workers in one pass

```bash
WS_KEY=$(cat ~/.ssh/id_ed25519.pub)
NODES="raspberrypi-23a7710c:192.168.1.21
raspberrypi-e3a771f1:192.168.1.22
raspberrypi-771be84c:192.168.1.23
raspberrypi-7386c525:192.168.1.24
raspberrypi-b814834e:192.168.1.124"

echo "$NODES" | while IFS=: read k8s ip; do
  kubectl --context home-k3s debug "node/${k8s}" --image=alpine:latest --profile=sysadmin \
    -- chroot /host sh -c "
      F=/home/kblack0610/.ssh/authorized_keys
      grep -qF '${WS_KEY}' \$F 2>/dev/null || {
        echo '${WS_KEY}' >> \$F
        chown kblack0610:kblack0610 \$F
        chmod 600 \$F
      }"
  # Verify before moving on
  ssh -o BatchMode=yes -o ConnectTimeout=5 "$ip" 'hostname' || echo "  STILL FAILING: $ip"
done

# Cleanup
kubectl --context home-k3s delete pods -n default -l '!app' --field-selector status.phase=Succeeded
```

If your shell prints "Creating debugging pod ..." but no command output for some nodes, that's the swallowed-stdout issue noted above — check `kubectl logs node-debugger-<name>-XXXXX` to confirm the actual exit, or just verify with the SSH probe.

## Other things you can do from a debug pod

Same `kubectl debug node/... -- chroot /host sh` opens the door for any host-state surgery when SSH is dead:

- `systemctl status <unit>` / `journalctl -u <unit>` — read service state
- `cat /etc/systemd/system/...` — read systemd configuration
- `vi /etc/...` — edit a config file (alpine ships `vi`; install `nano` with `apk add nano` if needed)
- `chmod 600 /home/<user>/.ssh/authorized_keys` — fix perm issues that block SSH

Be deliberate about destructive operations from here — there's no audit log of what you did in a debug pod beyond the pod's stdout. Prefer Ansible for anything that should be reproducible; reach for `kubectl debug` for the one-shot rescue.

## What this is NOT a substitute for

- **An Ansible role for SSH key management.** The recipe above is the rescue path, not the standing-state. If we keep re-using it, that's a signal to write `ansible/roles/authorized-keys` instead.
- **Recovering from a dead kubelet.** If the node shows `NotReady` in `kubectl get nodes`, the debug pod won't schedule there. You'd need IPMI / console / a PXE re-image.
- **Touching k8s workloads.** For pod-level shell-in operations see `docs/container-access.md` — `kubectl exec`, ephemeral debug containers, `kubectl cp`.

## When to update this doc

- When a new node is added or renamed — refresh the mapping table.
- When the PXE-seeded key rotates (`disk-install.sh:462`) — note the new key.
- When we add an Ansible role for `authorized_keys` propagation — point the recipe at it instead and demote the kubectl-debug path to "use only when Ansible can't reach the host."
