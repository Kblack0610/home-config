# crash-evidence

Makes a host that dies without warning leave evidence behind. Bound to the `adguard` group (pi3), because the whole house resolves through it.

## Why

pi3 has frozen three times (2026-08-09, 2026-08-23, 2026-09-30) and each time its journal simply stops: no error, no shutdown, nothing to read afterwards. A freeze never writes its own cause to the SD card. The power theory rests on 145 undervoltage events logged in August, but Prometheus samples once a minute and saw nothing on 2026-09-30, so it can neither confirm nor rule out a short brownout.

`roles/hw-watchdog` makes the Pi recover. This role makes the next freeze explain itself.

## What it does

1. **netconsole.** The kernel sends every console message over UDP to pi5-master (`roles/netconsole-receiver`) the moment it is printed. Lines written in the last second before a freeze survive even though they never reach this host's disk. The console loglevel is raised from the image's `quiet` 3 to 5, so ERR and WARNING lines (RCU stalls, hung tasks, mmc errors) are sent, not only CRIT and above.
2. **Undervoltage latch.** `vcgencmd get_throttled` bits 16-19 latch from boot until the next reboot, so a dip lasting milliseconds still sets them. A one-minute timer exports them to node-exporter's textfile collector as `rpi_throttled{flag,when}`, and `PiUndervoltageSinceBoot` alerts on it.

## Reading the evidence

```bash
# Kernel messages from pi3, stamped with the time pi5-master received them
ssh kblack0610@192.168.1.20 'journalctl -u netconsole-receiver --since "-1h"'

# Throttle flags right now
curl -s 'http://192.168.1.20:30090/api/v1/query?query=rpi_throttled' | jq -r '.data.result[] | "\(.metric.flag) \(.metric.when) \(.value[1])"'
```

`netcheck` (infrastructure/infra) prints the last netconsole lines as part of its evidence section.

## Run it

```bash
cd ansible
ansible-playbook playbooks/site.yml --limit pi5-master,pi3-adguard --tags crash-evidence --check --diff
ansible-playbook playbooks/site.yml --limit pi5-master,pi3-adguard --tags crash-evidence
```

Prove the path end to end by printing a test line into pi3's kernel log and finding it on pi5-master:

```bash
ssh kblack0610@192.168.1.193 'echo "<3>netconsole test $(date +%s)" | sudo tee /dev/kmsg'
ssh kblack0610@192.168.1.20 'journalctl -u netconsole-receiver -n 3'
```
