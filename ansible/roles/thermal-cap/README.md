# thermal-cap

Caps CPU power on the laptops that run CI, so a long job holds them at a temperature they can live with instead of at their thermal limit. It also stops the battery charging at a set percent. A oneshot unit reapplies everything at boot, since it all lives in sysfs.

## Why

On 2026-10-23 the Unity nightly held asus-laptop (Ryzen 9 5900HX) at 93-96C for hours, and it dropped off the network about 21:40 UTC with nothing in its journal. hp-victus (i7-13700HX) spent 74 minutes above 90C that day and peaked at 98C, against a 100C critical limit. Neither host had any cap on clocks or power; the CI slices only capped cores and memory (see `roles/ci-host-budget`).

## Why power and not CPU time

CPUQuota cannot be used here: it freezes a cgroup for the rest of each 100 ms period, which broke the Unity tests' millisecond timings (2026-10-05). A power cap slows every core evenly and all the time, so timings get uniformly a little slower instead of randomly a lot slower. That is easier on the tests than thermal throttling at 98C, which is uneven too.

## Knobs (set per host in `inventory.yml`, group `thermal_capped`)

| Variable | What it writes | Use on |
|---|---|---|
| `thermal_cap_platform_profile` | `/sys/firmware/acpi/platform_profile` | Hosts whose firmware offers it (asus: quiet/balanced/performance; hp-victus has none) |
| `thermal_cap_boost: false` | amd-pstate `cpufreq/boost=0`, or intel_pstate `no_turbo=1` | AMD, which has no writable package power limit |
| `thermal_cap_rapl_long_watts` / `_short_watts` | Intel RAPL PL1/PL2 on both the MSR and MMIO zones | Intel; the efficient cap, since the last watts buy the least speed |
| `thermal_cap_charge_limit` | `BAT*/charge_control_end_threshold` | Laptops on the charger 24/7 (asus has it; hp-victus does not) |

A knob that is set but whose file is missing, or that will not take the value (a BIOS-locked RAPL MSR, say), fails the unit and the play.

## Run it

```bash
cd ansible
ansible-playbook playbooks/site.yml --limit thermal_capped --tags thermal --check --diff
ansible-playbook playbooks/site.yml --limit thermal_capped --tags thermal
```

## Verify

```bash
systemctl status thermal-cap.service     # active (exited); journal lists each value written
```

Then watch a full nightly in Prometheus: `NodeCpuHot` (apps/monitoring/prometheus-rules-node-health.yaml) should stay quiet, and the target is at most 85C sustained. If it does not hold, lower the cap a step and compare shard times against the previous nightly.
