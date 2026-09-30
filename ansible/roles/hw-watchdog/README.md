# hw-watchdog

Arms the SoC hardware watchdog through systemd, and makes a kernel panic reboot instead of hang. A host that freezes then resets itself in about 15 seconds instead of staying dead until someone pulls the plug.

## Why

2026-09-30 pi3-adguard froze at about 14:24 and the house had no DNS for 25 minutes, because the router forwards every query to that one Pi (see `ansible/roles/adguard/README.md`, "Why there is no DNS fallback"). It came back only when a human power-cycled the router and then the Pi.

Evidence gathered afterwards:

- The Pi's journal for that boot stops mid-stream at 14:17. No shutdown, no kernel error, no OOM kill. Prometheus shows 640 MB free, load 0, 37C and an idle disk right up to the last scrape, so it was not resource exhaustion.
- It was the third time. The boots ending 2026-08-09 and 2026-08-23 stop the same silent way.
- The 2026-08-09 boot logged 145 `Undervoltage detected!` events, so the likely root cause is the power supply. This role does not fix that; it limits the damage.
- `/dev/watchdog0` (bcm2835-wdt) was present the whole time, but systemd ships `RuntimeWatchdogSec=0`, so nothing ever armed it.

## How it works

The watchdog is a countdown timer inside the SoC, separate from the CPU cores. With `RuntimeWatchdogSec=15s`, systemd (PID 1) resets it every 7.5s. If the kernel freezes, PID 1 stops running, the countdown reaches zero and the timer pulls the chip's reset line. No software has to be alive for that to happen.

`kernel.panic=10` covers the other case: a kernel that panics but keeps the timer fed. The default of 0 means a panicked kernel sits there forever.

## What it does not cover

- **AdGuard hung while the OS is fine.** PID 1 keeps petting the watchdog, so nothing reboots. The compose healthcheck marks the container `unhealthy`, but Docker's restart policy only acts on exit.
- **A brownout that wedges the SoC itself.** The timer lives on the same chip. Usually it still fires, but not always, which is why the power supply is the real fix.

## Run it

```bash
cd ansible   # ansible.cfg uses relative paths; see docs/ansible.md

ansible-playbook playbooks/site.yml --limit pi3-adguard --tags watchdog --check --diff
ansible-playbook playbooks/site.yml --limit pi3-adguard --tags watchdog
```

Applying it re-executes systemd in place (`daemon-reexec`). No services restart, so house DNS stays up.

## Verify

```bash
ssh kblack0610@192.168.1.193 'systemctl show -p RuntimeWatchdogUSec -p RebootWatchdogUSec; cat /proc/sys/kernel/panic; journalctl -b | grep -i "hardware watchdog"'
# RuntimeWatchdogUSec=15s, RebootWatchdogUSec=2min, 10
```

This kernel is built without `CONFIG_WATCHDOG_SYSFS`, so `/sys/class/watchdog/watchdog0/` has no `state` or `timeout` files. Read the state from systemd, as above.
