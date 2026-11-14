# dgpu-off

Switches off the discrete NVIDIA GPU on a headless host: no driver module loads, `nvidia-persistenced` is off, and runtime PM lets the unbound card drop to D3cold. Bound to the `dgpu_off` inventory group (hp-victus).

## Why

hp-victus (HP Victus laptop, RTX 4060 Laptop GPU, driver 595.58.03 open) hung on 2026-11-12 at 15:02 and again on 2026-11-13 at 15:35, and both times a human had to power-cycle it. Since 2026-09-28 its kernel log had shown the GSP firmware restarting every few minutes:

```
NVRM: _kgspProcessRpcEvent: Attempted to process RPC event from GPU0: 0x101a (PFM_REQ_HNDLR_STATE_SYNC_CALLBACK) during bootup without API lock
NVRM: _kgspIsHeartbeatTimedOut: Heartbeat timed out, currentTimeMs ... heartbeat 0 ...
```

That came to 205 timeouts on the boot that ended 11-13, plus `NV_ERR_NO_MEMORY` allocation failures on 11-11. Each time the card woke, its GSP booted again and raced a Dynamic Boost (PFM) event from the laptop firmware. Nothing used the GPU: the only pod requesting it was the NVIDIA device plugin, and the human ruled hp-victus out of GPU work on 2026-11-13. Even so, the card idled in P3 at 20 W with a 2250 MHz core clock, on heatpipes it shares with the CPU. During the last half hour before the 11-13 hang, the CPU reached 98C of its 100C limit and the DIMMs 80C of 85C, all under the 32/40 W RAPL cap (`roles/thermal-cap`).

Switching the card off removes the driver fault and that heat. Pinning an older driver would keep both.

## What it does

1. `/etc/modprobe.d/dgpu-off.conf`: `blacklist` and `install <mod> /bin/false` for `nvidia`, `nvidia_modeset`, `nvidia_uvm`, `nvidia_drm` and `nouveau`. `install` also blocks an explicit `modprobe`, such as the one nvidia-smi or the container runtime would run.
2. Strips those modules from `MODULES=()` in `/etc/mkinitcpio.conf`, removes the PXE `nvidia-gpu.sh` modprobe files, and runs `mkinitcpio -P`.
3. Disables `nvidia-persistenced`.
4. A udev rule sets `power/control=auto` on every NVIDIA PCI function, the HDMI audio function included. While any function stays awake, the card cannot reach D3cold.
5. Unloads the modules now, without waiting for a reboot. This step fails while something still holds `/dev/nvidia*`.

The `nvidia` RuntimeClass that k3s registers stays, but nothing uses it, and with no device plugin the node advertises no `nvidia.com/gpu`.

## Run it

```bash
cd ansible
ansible-playbook playbooks/site.yml --limit dgpu_off --tags dgpu-off --check --diff
ansible-playbook playbooks/site.yml --limit dgpu_off --tags dgpu-off
```

## Verify

```bash
ssh hp-victus 'lsmod | grep -c nvidia; cat /sys/bus/pci/devices/0000:01:00.0/power_state; grep MODULES= /etc/mkinitcpio.conf'
# expect: 0, D3cold, no nvidia in MODULES
ssh hp-victus 'journalctl -k -b | grep -c NVRM'   # 0 after the next boot
```

Do not run `nvidia-smi` to check. With the module blocked it only fails, and on a host that still loads the driver it wakes the card.

## Undo

Remove the host from `dgpu_off`, delete `/etc/modprobe.d/dgpu-off.conf` and `/etc/udev/rules.d/80-dgpu-off.rules`, put the modules back in `MODULES=()`, run `mkinitcpio -P`, and reboot.
