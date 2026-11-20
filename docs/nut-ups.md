# UPS protection: NUT on every host on the UPS

A single CyberPower CP1500PFCLCD plugged into `pi5-master` via USB carries every host in the table below. When grid power fails, the NUT primary on `pi5-master` pages `homelab-alerts`, then waits out a 300-second settling window (an outage shorter than that changes nothing). If power is still out, it broadcasts FSD. Low battery (300 s of runtime left) ends the wait early, but only while the UPS is on battery: upsmon treats OB+LB as critical itself, and a low-battery flag on line power is ignored. Each secondary catches the FSD, stops `k3s-agent` where it has one so pods receive `SIGTERM` with their full `terminationGracePeriodSeconds`, then halts. The master waits 60 s (`FINALDELAY`) so the secondaries get a head start, halts, and tells the UPS to cut its outlets (killpower), which prevents deep battery discharge.

Killpower cuts every battery outlet, so **every host on a battery outlet must be a NUT secondary**. A host that is not gets hard-cut at the end of the primary's shutdown; that is what took the workstation down on 2026-11-17.

See `docs/ansible.md` for the broader host-layer workflow; this doc covers the UPS specifics.

## Hosts on the UPS

From the 2026-11-17 outage: Prometheus scrapes show which hosts dropped the instant the power went (not on battery) and which stayed up until killpower (on battery).

| Host | IP | Outlet | NUT |
|---|---|---|---|
| pi5-master | .20 | battery | primary (USB to the UPS) |
| cachyos-x8664-main (the workstation) | .2 | battery | secondary, local (become password: `ansible/README.md`, Secrets) |
| mac-mini | .7 | battery | secondary (Homebrew `nut`, LaunchDaemon `me.kblab.nut-upsmon`) |
| ms-a2 | .28 | battery | secondary |
| deskslim-2 | .29 | battery | secondary |
| deskslim-1 | .19 | power strip on the UPS, outlet not checked (2026-11-18) | secondary |
| tuf-b550 | .8 | living room, outlet not checked (2026-11-20) | secondary |
| pi3-adguard | .193 | battery | secondary |
| pi5-worker1, pi5-worker2, pi5-worker3, pi4-worker4, pi4-worker5 | .21 to .24, .124 | not on battery: dropped at power loss | secondary, ready for when they move to battery outlets |

Load at idle with all of the above is about 38% of 1000 W, with about 950 s of runtime (`upsc cyberpower ups.load battery.runtime`). A new host on the UPS: add it to `nut_clients` in `ansible/inventory.yml`, apply the NUT play to it, and add its row here.

## Alerts

`pi5-master` pages `https://ntfy.kblab.me/homelab-alerts` (subscribe in the ntfy app) on ONBATT, ONLINE, LOWBATT, FSD, COMMBAD, NOCOMM and REPLBATT, and once more when it starts the shutdown. ntfy runs in the cluster, so a page sent during an outage can fail. The primary's SHUTDOWNCMD (`primary-shutdown.sh`) therefore leaves a marker, and on the next boot `nut-outage-report.service` pages "UPS shutdown recovered", retrying for about 10 minutes while the cluster comes back. The master's journal is persistent (Raspberry Pi OS ships it volatile), so the event log of an outage survives it.

## Hardware

- **UPS**: CyberPower CP1500PFCLCD (1500 VA / 1000 W pure sine wave).
- **Driver**: `usbhid-ups`: CyberPower's HID profile is well-supported in NUT 2.7 and later.
- **Cable**: standard USB-A to USB-B (included with the UPS).
- **Server node**: `pi5-master` (192.168.1.20). Plug the USB cable here.

> **Naming**: `pi5-master` is the **Ansible inventory alias**: works only with `ansible-playbook --limit`. For ad-hoc SSH use the DHCP-assigned LAN name `pi5-master-lan` (resolvable via the OpenWRT dnsmasq) or the IP directly. Same pattern for workers: `pi5-worker2-lan`, `pi4-worker4-lan`, etc. The DHCP table is `infrastructure/dhcp/devices.yaml`.

## One-time setup

1. **Pick a strong password** for the upsd `upsmon` user. At least 12 chars, mix of classes. This is the credential every secondary uses to authenticate to the master, so it gates the FSD broadcast: treat it like SSH.

2. **Stash it in the global SOPS file**:

    ```bash
    sops ansible/group_vars/all/vault.sops.yaml   # set vault_nut_upsmon_password
    ```

    sops decrypts into `$EDITOR` and re-encrypts on save; only the encrypted file touches disk and it is safe to commit.

3. **Verify USB enumeration** before running Ansible. SSH to the master and look for the UPS:

    ```bash
    ssh pi5-master-lan 'lsusb | grep -i cyber'
    # Bus 003 Device 002: ID 0764:0601 Cyber Power System, Inc. PR1500LCDRT2U UPS
    ```

    If you don't see it, the cable isn't seated or the kernel didn't enumerate. Fix that before continuing: Ansible can't make a USB cable plug itself in.

4. **Dry-run the play** to see what'll change:

    ```bash
    cd ansible
    ansible-playbook playbooks/site.yml --tags nut --check --diff \
      --limit nut_server:nut_clients
    ```

5. **Apply against the master first**:

    ```bash
    ansible-playbook playbooks/site.yml --tags nut --limit pi5-master
    ssh pi5-master-lan 'upsc cyberpower | head -20'
    ```

    You should see a metric dump: `battery.charge: 100`, `ups.load: 12`, `ups.status: OL` (online).

6. **Apply against one worker and confirm it reaches the server**:

    ```bash
    ansible-playbook playbooks/site.yml --tags nut --limit pi5-worker2
    ssh pi5-worker2-lan 'upsc cyberpower@pi5-master-lan | head -20'
    ```

7. **Roll out to the rest**:

    ```bash
    ansible-playbook playbooks/site.yml --tags nut --limit nut_clients
    ```

8. **Idempotency check**: re-run the same command. Expect `changed=0`. If anything reports `changed`, the role isn't idempotent against your environment: open an issue before relying on it.

## Outage drill

Schedule this for a quiet hour. Every host on the UPS shuts down and reboots, including the workstation and the mac-mini, and pods restart.

1. Note the baseline: `kubectl get nodes`, `kubectl get pods -A | grep -v Running | head`.
2. **Pull the UPS plug from the wall.** Don't power off the UPS itself: pulling the wall plug simulates a real outage cleanly.
3. Watch the master:

    ```bash
    ssh pi5-master-lan 'journalctl -u nut-monitor -u nut-server -f'
    ```

    You'll see `ONBATT` immediately and get the "UPS on battery" page. After 300s (the settling timer), `nut-upssched-cmd` runs and you'll see `power-loss shutdown (reason=onbatt-settle): broadcasting FSD`, then `FSD broadcast ok`.

4. Secondaries start halting within a few seconds of FSD; the mac-mini sleeps instead. The master halts 60s later (`FINALDELAY`), and the UPS outlets click off about 20s after that (`ups.delay.shutdown`).

5. **Plug the UPS back in.** The outlets re-energise once the UPS finishes its boot self-test (10 to 30s). Hosts boot and rejoin the cluster; within about 10 minutes the "UPS shutdown recovered" page arrives.

6. Verify: `kubectl get nodes` shows all Ready, and `kubectl get pods -A | grep -v Running` is empty (or transient terminations). On the master, `journalctl -b -1 -t nut-notifycmd -t nut-upssched-cmd -t nut-cluster-shutdown` shows the previous boot's events.

## Day-to-day commands

```bash
# Inspect UPS metrics from any client
upsc cyberpower@pi5-master-lan

# What the master logged, without SSH (its journal ships to Loki): USB drops, NUT events
scripts/host-postmortem.py raspberrypi now --before 1h

# Watch event flow live on the master
ssh pi5-master-lan 'journalctl -t nut-notifycmd -t nut-upssched-cmd -t nut-cluster-shutdown -f'

# Reload upsmon.conf after a hand edit (safe, triggers nothing)
ssh pi5-master-lan 'sudo upsmon -c reload'

# Which hosts are logged in to upsd (one IP per secondary, plus 127.0.0.1)
ssh pi5-master-lan 'upsc -c cyberpower'

# Last outage's events, from the boot before this one
ssh pi5-master-lan 'journalctl -b -1 -t nut-notifycmd -t nut-upssched-cmd -t nut-cluster-shutdown -t nut-primary-shutdown'

# Tighten / loosen the settling timer (default 300s)
# Edit defaults/main.yml or group_vars/nut_server/main.yml: nut_onbatt_settle_seconds
ansible-playbook playbooks/site.yml --tags nut --limit pi5-master

# Apply to the workstation (local connection; the sudo password is rbw `ansible_password`, see ansible/README.md Secrets)
ansible-playbook playbooks/site.yml --tags nut --limit cachyos-x8664-main --become-password-file "$p"
```

## Recovery

- **upsd not responding**: `sudo systemctl status nut-server nut-driver@cyberpower`. The driver unit is the usual culprit if the USB cable was unplugged.
- **Client can't reach upsd**: from the client, `nc -vz pi5-master-lan 3493`. If the port is closed, check the master's `LISTEN` directive in `/etc/nut/upsd.conf` and any firewall.
- **UPS stuck "killed power"**: after the OS halts NUT issues `killpower` to the UPS, which cuts the outlets for a few seconds. If the outlets stay off after grid returns, the UPS battery may be exhausted; let it charge for 10+ minutes with the unit plugged in, then power-cycle the UPS via its front-panel button.
- **Forgot the upsmon password**: `sops -d --extract '["vault_nut_upsmon_password"]' ansible/group_vars/all/vault.sops.yaml` (requires `~/.config/sops/age/keys.txt`: see `CLAUDE.md`).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| A host on the UPS went dark about a minute into an outage with no clean shutdown in its journal, and the UPS still had most of its battery (2026-11-17, 01:39 PST: the workstation, mac-mini, ms-a2, deskslim-2, pi3-adguard) | The primary's FSD ran after the 60s settle, and its killpower cut every battery outlet. Only the Pis were NUT secondaries, so the rest were hard-cut. | Every host on a battery outlet is now in `nut_clients`, and the settle is 300s. Check `upsc -c cyberpower` lists each host. |
| An outage left no record anywhere | The master's journal was volatile and nothing paged. | Persistent journal on the master, ntfy pages, and the after-boot "UPS shutdown recovered" page (see Alerts). |
| Pi workers die the instant the power goes, before any NUT event | They are not on battery outlets (2026-11-17: .22 to .24 by 01:38:29, .21 and .124 by 01:38:47). | Move them to the UPS's Battery+Surge bank; nothing in software can fix it. |
| Every host shut down at once with no outage: master log shows `LOWBATT` with no `ONBATT` before it (2026-11-17 08:37) | The UPS raises LB on line power whenever `battery.runtime` falls to `battery.runtime.low` (300 s), which a full battery reaches near 78% load; an upssched `AT LOWBATT` rule ran the shutdown on it. | Rule removed; upsmon acts on LB only together with OB (`tests/run-tests.sh` replays both). LB on line power still pages: it means the load leaves 5 minutes of runtime. |
| A secondary is up after an outage but missing from `upsc -c cyberpower` | Arch ships `nut.target` disabled and `nut-monitor` is WantedBy it, so upsmon never started at boot (ms-a2, deskslim-2, 2026-11-17). | The role enables `nut.target`. Check with `systemctl is-active nut-monitor`. |
| The mac-mini stayed off after the outlets came back, though `autorestart` was 1 (2026-11-17) | NUT halted it cleanly before killpower, and macOS boots after a power loss only if it was on when the power went. | Its `worker-shutdown.sh` runs `pmset sleepnow` instead of halting, so killpower cuts it while asleep and `autorestart 1` (set by the role) boots it. Check `pmset -g \| grep autorestart`. |
| The UPS beeps on line power with no page, or its USB link drops every few minutes (kernel `USB disconnect`, driver `nut_libusb_get_report: No such device`, `UpsUsbFlapping` fires) | The UPS drops off the bus itself: the drops kept coming on 2026-11-17 after the master moved to another supply and read `0x0`, and after `pollonly` (09:30 PST): 3 to 7 an hour before, 3 to 39 an hour after, plus about 15,000 driver errors an hour. NUT recovers each drop without a page; data went stale 6 times that day. The drops then stopped at 19:37 PST with no config or cable change (139 after `pollonly`, longest gap 45 min, then none for 2h49m), so `pollonly` is not the fix. The driver error rate stays flat at about 15,400 an hour whether the link drops or not, so count kernel `USB disconnect` lines, not driver errors. | Read the drops without SSH: `scripts/host-postmortem.py raspberrypi now --before 1h`. Stop the driver for 30 minutes: drops with nothing polling mean the cable, port or UPS, so move it to the black USB 2 port on a short shielded cable. |
| A Pi logs undervoltage (`vcgencmd get_throttled` not `0x0`, `vcgencmd pmic_read_adc EXT5V_V` under 5.0V) | Its 5V supply sags. On 2026-11-17 the master's supply was the culprit; swapped onto pi5-worker3, the sag followed it. | Replace the supply (official 27W, 5.1V 5A; why nothing else works: [`baremetal-power.md`](./baremetal-power.md) section 4). `PiUndervoltageSinceBoot` fires for any Pi on a weak supply (`roles/rpi-throttle-metric`). |
| The cluster stays cordoned or Flux suspended after an outage | Older versions of `cluster-graceful-shutdown.sh` tried to cordon and suspend; removed 2026-11-17 because they never ran (the nut user cannot read the kubeconfig) and could not be undone automatically. | `kubectl uncordon <node>`, `flux resume kustomization --all`. |

## Followups (not done yet)

- **openwrt-router**: OpenWRT has the `nut` package. Different config dir (`/etc/config/nut_*`); separate role pass.
- **Prometheus exporter**: `nut_exporter` running next to upsd would feed `battery.charge`, `ups.load`, `input.voltage` into the Homelab dashboard. Today we only get the event log.
