# UPS protection: NUT on the k3s fleet

A single CyberPower CP1500PFCLCD plugged into `pi5-master` via USB protects every Pi in the cluster. When grid power fails, the NUT primary on `pi5-master` waits out a 60-second settling window (so a brownout doesn't trigger anything), then suspends Flux, cordons every node, and broadcasts FSD. Each worker's NUT secondary catches the FSD, stops `k3s-agent` cleanly so pods receive `SIGTERM` with their full `terminationGracePeriodSeconds`, then halts. The master halts last and tells the UPS to cut its outlets — preventing deep battery discharge.

See `docs/ansible.md` for the broader host-layer workflow; this doc covers the UPS specifics.

## Hardware

- **UPS**: CyberPower CP1500PFCLCD (1500 VA / 1000 W pure sine wave).
- **Driver**: `usbhid-ups` — CyberPower's HID profile is well-supported in NUT ≥2.7.
- **Cable**: standard USB-A → USB-B (included with the UPS).
- **Server node**: `pi5-master` (192.168.1.20). Plug the USB cable here.

> **Naming**: `pi5-master` is the **Ansible inventory alias** — works only with `ansible-playbook --limit`. For ad-hoc SSH use the DHCP-assigned LAN name `pi5-master-lan` (resolvable via the OpenWRT dnsmasq) or the IP directly. Same pattern for workers: `pi5-worker2-lan`, `pi4-worker4-lan`, etc. The DHCP table is `infrastructure/dhcp/devices.yaml`.

## One-time setup

1. **Pick a strong password** for the upsd `upsmon` user. ≥12 chars, mix of classes. This is the credential every secondary uses to authenticate to the master, so it gates the FSD broadcast — treat it like SSH.

2. **Stash it in the global vault**:

    ```bash
    cd ansible
    cp group_vars/all/vault.yml.example group_vars/all/vault.yml
    $EDITOR group_vars/all/vault.yml          # paste the password
    ansible-vault encrypt group_vars/all/vault.yml
    ```

    The encrypted file is safe to commit. The plaintext `.example` is the template.

3. **Verify USB enumeration** before running Ansible. SSH to the master and look for the UPS:

    ```bash
    ssh pi5-master-lan 'lsusb | grep -i cyber'
    # → Bus 001 Device 003: ID 0764:0501 Cyber Power System, Inc. CP1500PFCLCD
    ```

    If you don't see it, the cable isn't seated or the kernel didn't enumerate. Fix that before continuing — Ansible can't make a USB cable plug itself in.

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

8. **Idempotency check**: re-run the same command. Expect `changed=0`. If anything reports `changed`, the role isn't idempotent against your environment — open an issue before relying on it.

## Outage drill

Schedule this for a quiet hour. Workers will reboot, pods will restart, Flux will be paused for ~5 minutes.

1. Note the baseline: `kubectl get nodes`, `kubectl get pods -A | grep -v Running | head`.
2. **Pull the UPS plug from the wall.** Don't power off the UPS itself — pulling the wall plug simulates a real outage cleanly.
3. Watch the master:

    ```bash
    ssh pi5-master-lan 'journalctl -u nut-monitor -u nut-server -f'
    ```

    You'll see `ONBATT` immediately. After 60s (the settling timer), `nut-upssched-cmd` runs, you'll see `starting graceful cluster shutdown`, then `flux suspend ok`, then `cordon node/... ok` for each node, then `FSD broadcast ok`.

4. Workers halt within ~10s of FSD. Master halts ~5s later (`FINALDELAY`). UPS outlets click off ~30s after that.

5. **Plug the UPS back in.** The outlets re-energise once the UPS finishes its boot self-test (10–30s). Pis PXE-boot or local-boot and rejoin the cluster.

6. After everything is back, **unsuspend Flux**:

    ```bash
    flux resume kustomization --all
    ```

7. Verify: `kubectl get nodes` → all Ready. `kubectl get pods -A | grep -v Running` → empty (or transient terminations).

## Day-to-day commands

```bash
# Inspect UPS metrics from any client
upsc cyberpower@pi5-master-lan

# Watch event flow live on the master
ssh pi5-master-lan 'journalctl -t nut-notifycmd -t nut-upssched-cmd -t nut-cluster-shutdown -f'

# Trigger a fake ONBATT (the timer will fire, then the graceful path — careful!)
ssh pi5-master-lan 'sudo upsmon -c reload'   # safe: just reloads upsmon.conf

# Tighten / loosen the settling timer (default 60s)
# Edit defaults/main.yml or group_vars/nut_server/main.yml: nut_onbatt_settle_seconds
ansible-playbook playbooks/site.yml --tags nut --limit pi5-master
```

## Recovery

- **upsd not responding**: `sudo systemctl status nut-server nut-driver@cyberpower`. The driver unit is the usual culprit if the USB cable was unplugged.
- **Client can't reach upsd**: from the client, `nc -vz pi5-master-lan 3493`. If the port is closed, check the master's `LISTEN` directive in `/etc/nut/upsd.conf` and any firewall.
- **UPS stuck "killed power"**: after the OS halts NUT issues `killpower` to the UPS, which cuts the outlets for a few seconds. If the outlets stay off after grid returns, the UPS battery may be exhausted; let it charge for 10+ minutes with the unit plugged in, then power-cycle the UPS via its front-panel button.
- **Forgot the vault password**: `sops -d apps/ansible-runner/secret.yaml | grep ansible-vault-pass` (requires `~/.config/sops/age/keys.txt` — see `CLAUDE.md`).

## Followups (not done yet)

- **pi5-worker1**: SSH key auth from the workstation is broken. Add it to the `nut_clients` group in `inventory.yml` once that's fixed.
- **mac-studio, mac-mini**: if they sit on the same UPS, install `nut` via Homebrew and add them to a `nut_clients_macos` group. Same `upsmon.client.conf` shape, different package + service paths.
- **openwrt-router**: OpenWRT has the `nut` package. Different config dir (`/etc/config/nut_*`); separate role pass.
- **Prometheus exporter**: `nut_exporter` running next to upsd would feed `battery.charge`, `ups.load`, `input.voltage` into the Homelab dashboard. Today we only get the event log.
