# thermal-cap

The fleet's thermal standard: every x86 Linux machine that carries load has its CPU power capped from setup, so a long load holds it at most 85C sustained instead of at its thermal limit. It also stops a laptop battery charging at a set percent. A oneshot unit reapplies everything at boot, since it all lives in sysfs.

## Who gets it

Group `thermal_capped` in `ansible/inventory.yml` takes its hosts from child groups (`linux_bare_metal`, `watchdog_x86`, `desktop_workstations`), so a host added to any of them is capped by the next apply with no edit to the group. An x86 Linux host outside them sets `thermal_cap_exempt: <why>` instead (a cloud VM, whose cooling the provider owns). `scripts/check-thermal-standard.py`, run in CI, fails on an x86 Linux host with neither, and on a capped host missing from the convergence check's `HOSTS` (`apps/ansible-runner/cronjob.yaml`). Pis, Macs, Windows and the router are out of scope: the role writes Linux x86 sysfs knobs.

A machine off the standard is red, by the human's ask of 2026-11-17: `NodeCpuHot` (`apps/monitoring/prometheus-rules-node-health.yaml`, above 90C for 10m) and `ConvergenceDrift` (a host where this role's apply would change something) are both `fleet: fix`, so the `alertmanager-fleet` watch queues a red item for an agent. The lever for either is this role.

## Why

Sustained CI load pushed asus-laptop (Ryzen 9 5900HX) to 93-96C and hp-victus (i7-13700HX) to 98C of a 100C critical limit, with no cap on clocks or power; the CI slices only capped cores and memory (see `roles/ci-host-budget`). In the 7 days to 2026-11-17, uncapped under Unity CI, ms-a2 (9955HX) peaked at 99.5C, deskslim-2 (9900X) at 97.6C, and the workstation (9950X3D) sat at a median 96.25C with a peak of 106.25C, past AMD's 95C Tjmax.

## Why power and not CPU time

CPUQuota cannot be used here: it freezes a cgroup for the rest of each 100 ms period, which broke the Unity tests' millisecond timings (2026-10-05). A power cap slows every core evenly and all the time, so timings get uniformly a little slower instead of randomly a lot slower. That is easier on the tests than thermal throttling at 98C, which is uneven too.

## Knobs

The group sets `thermal_cap_boost: false`, the lever on AMD. A host with a better lever overrides it in `inventory.yml` (hp-victus keeps turbo and caps RAPL instead).

| Variable | What it writes | Use on |
|---|---|---|
| `thermal_cap_platform_profile` | `/sys/firmware/acpi/platform_profile` | Hosts whose firmware offers it (asus: quiet/balanced/performance; hp-victus has none) |
| `thermal_cap_boost: false` | amd-pstate `cpufreq/boost=0`, or intel_pstate `no_turbo=1` | AMD, which has no writable package power limit (the RAPL zone has no constraint files); the group default |
| `thermal_cap_rapl_long_watts` / `_short_watts` | Intel RAPL PL1/PL2 on both the MSR and MMIO zones | Intel; the efficient cap, since the last watts buy the least speed |
| `thermal_cap_charge_limit` | `BAT*/charge_control_end_threshold` | Laptops on the charger 24/7 (asus has it; hp-victus does not) |

A knob that is set but whose file is missing, or that will not take the value (a BIOS-locked RAPL MSR, say), fails the unit and the play.

Boost off costs speed: cores run at base clock, 2.5GHz on ms-a2's 9955HX (boost to 5.06GHz), 4.4GHz on deskslim-2's 9900X, 4.3GHz on the workstation's 9950X3D. Compare shard times against the previous nightly after a change.

## Run it

```bash
cd ansible
ansible-playbook playbooks/site.yml --limit thermal_capped --tags thermal --check --diff
ansible-playbook playbooks/site.yml --limit thermal_capped --tags thermal
```

The workstation is managed over a local connection and its sudo asks for a password, so the human applies it: `ansible-playbook playbooks/site.yml --limit cachyos-x8664-main --tags thermal --ask-become-pass`.

## Verify

```bash
systemctl status thermal-cap.service     # active (exited); journal lists each value written
cat /sys/devices/system/cpu/cpufreq/boost   # 0 on a boost-capped host
```

Then watch the host under load in Prometheus (`instance:node_cpu_package_temp:celsius`): `NodeCpuHot` should stay quiet, and the target is at most 85C sustained. If it does not hold, lower the cap a step and compare shard times against the previous nightly.
