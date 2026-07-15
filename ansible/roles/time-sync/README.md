# time-sync

Keeps every Linux host's clock synchronized with `systemd-timesyncd`.

## Why

`hp-victus` was discovered running **~102 seconds behind** the rest of the cluster with **no time daemon at all** — `systemd-timesyncd` disabled and inactive, `chronyd`/`ntpd` not even installed — while every other node sat within a second:

```
hp-victus    skew= -102.3s  sync_status=0
pi5-master   skew=   +0.7s  sync_status=1
asus-laptop  skew=   +0.8s  sync_status=1
```

It surfaced by accident. `apps/fleet-exporter` got scheduled onto hp-victus and read gatus's results — stamped by asus-laptop's *correct* clock — as ~54s in the **future**, so every machine's age went negative and the whole fleet rendered DOWN. Nothing was down.

That was the cheap symptom. The expensive ones don't announce themselves as clock problems:

- **TLS validation** — certificates are time-bound; a skewed host can reject valid certs or accept expired ones.
- **Log ordering** — events across nodes interleave wrongly, so a timeline built from them is fiction.
- **Kubernetes leases and token expiry** — both assume nodes agree on the time.

## Why timesyncd and not chrony

It's already what every healthy node runs (`enabled/active` on the Pis), it ships inside systemd so there's nothing to install on Arch, and matching the rest of the fleet beats introducing a second time daemon to reason about.

## What it does

1. Ensures the package exists (a no-op on Arch, where timesyncd is part of systemd — hence `failed_when: false`).
2. Enables and starts `systemd-timesyncd`.
3. Runs `timedatectl set-ntp true`. **This step is not redundant**: hp-victus reported `NTP service: inactive` even with the unit present — `systemctl enable` and the timedate1 `NTP=yes` property are different facts.
4. **Polls until `NTPSynchronized=yes`, and FAILS if it never does.** Enabling a unit and having a correct clock are not the same thing; a run that "succeeds" while the clock is still wrong just recreates the bug we're fixing.

## Run it

```bash
# check first, always
ansible-playbook ansible/playbooks/site.yml --tags time --limit hp-victus --check --diff
ansible-playbook ansible/playbooks/site.yml --tags time --limit hp-victus
```

Verify from anywhere, without SSH — node-exporter already exposes it:

```bash
curl -s http://192.168.1.243:9100/metrics | grep -E '^node_timex_sync_status|^node_time_seconds'
# node_timex_sync_status 1   <- 1 = synced, 0 = not
```

`node_timex_sync_status` is the honest check and it's already scraped by Prometheus on every node, so this is worth an alert rule — an unsynced clock is currently only visible if you go looking.

## Note on `--limit`

Use the bare inventory alias (`hp-victus`), **not** a `-lan` DHCP name — see the host-access table in `CLAUDE.md`.
