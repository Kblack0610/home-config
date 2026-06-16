# Bare-metal power resilience (physical checklist)

**Why this exists:** a grid power outage took down `thinkcentre` (192.168.1.100,
x86 k3s workstation node) and it **stayed dark** — a reset didn't bring it back.
Two root causes, both physical and neither fixable from software:

1. Its BIOS was not set to power on automatically after AC loss.
2. It is (probably) not on a UPS battery outlet, so it hard-dropped instead of
   shutting down gracefully.

The software side of the incident is handled in git (CI runners self-heal across
nodes, a `NodeDown` alert now pages via ntfy, logs are centralized in Loki). This
doc covers the **hands-on steps only you can do at the machine**.

> Host name/IP cheatsheet for the boxes below — see
> [`host-access.md`](./host-access.md). Bare-metal x86 nodes:
> `thinkcentre` 192.168.1.100, `hp-victus` 192.168.1.243, `asus-laptop` 192.168.1.152.

---

## 1. BIOS: "Restore on AC Power Loss" → **Power On** (the real fix)

This single setting is what makes a box reboot itself after an outage. Without
it, the machine waits for a human to press the power button — exactly what
happened to thinkcentre.

### thinkcentre (Lenovo desktop)
1. Power on, tap **F1** (or **Enter** → F1) at the Lenovo splash to enter BIOS.
2. **Power** → **After Power Loss** (some firmwares: **Automatic Power On** →
   **After AC Power Loss**).
3. Set to **Power On** (not "Last State", which can stay off if it was off when
   the power dropped — prefer the unconditional **Power On**).
4. **F10** to save and exit.

### hp-victus / asus-laptop (laptops)
Laptops have internal batteries, so they ride through brief outages — but a long
outage that drains the battery has the same "stays off" failure. Where the
firmware exposes it:
- HP: **F10** at boot → **Advanced** → **Boot Options** / **Power-On Options** →
  enable **AC Recovery / After Power Loss = Power On** if present.
- ASUS: **F2** / **Del** at boot → **Advanced** → **APM** → **Restore AC Power
  Loss = Power On** if present.
- If the laptop firmware has no such option (common), rely on the internal
  battery + the UPS for the desktop; document here that it's vendor-default.

> After setting this, a **controlled** power-pull test (your call, when nothing
> important is running) should show the machine auto-booting and rejoining the
> cluster within a couple of minutes.

---

## 2. UPS: put thinkcentre on a **battery** outlet

The homelab UPS is a CyberPower CP1500PFCLCD on `pi5-master`
(see [`nut-ups.md`](./nut-ups.md)). Desktops are the real exposure (no internal
battery), so thinkcentre should be on a battery-backed outlet.

1. On the CP1500 rear, the outlets are split into a **Battery+Surge** bank and a
   **Surge-only** bank. Move thinkcentre's plug to a **Battery+Surge** outlet.
2. Check headroom: a CP1500 (~900W / 1500VA) already drives the Pi fleet + UPS
   electronics. A desktop under load can be 100–250W — confirm the runtime on the
   LCD stays sane (several minutes) after adding it. If runtime drops too low,
   don't add it; note the limit here instead.
3. Once it's on battery, bring it under **NUT** so it shuts down gracefully on a
   sustained outage instead of hard-dropping — add `thinkcentre` to `nut_clients`
   in `ansible/inventory.yml` and re-run the NUT play (deferred follow-up; see
   [`nut-ups.md`](./nut-ups.md)).

---

## 3. After any reboot: verify the NIC actually came back

This incident's tell: thinkcentre reset but **never rejoined the LAN** (the
router showed ARP `0x0` for it, the k8s node stayed `NotReady`). That points at a
physical link problem, not the OS.

1. Check the **NIC link LED** on the motherboard/switch port — solid/blinking =
   link, dark = no link.
2. Reseat the Ethernet cable both ends; try a different switch port.
3. From another host: `ping 192.168.1.100`, then `arp -n | grep 192.168.1.100`
   (a `0x0` / incomplete entry = no link).
4. Once it pings, confirm it rejoined: `kubectl --context home-k3s get nodes`
   → `thinkcentre` should return to `Ready`, and the Forgejo runner / node-exporter
   land back on it automatically.

---

## Quick verification after the outage is fully resolved

```bash
# Node back in the cluster
kubectl --context home-k3s get nodes -o wide | grep thinkcentre   # want: Ready

# node-exporter scraping again (NodeDown clears)
#   prometheus.kblab.me → up{instance="192.168.1.100:9100"} == 1

# Runners healthy across nodes (no longer a single point of failure)
kubectl --context home-k3s -n forgejo get pods -o wide
```

## Related docs
- [`host-access.md`](./host-access.md) — the four-names-per-host gotcha
- [`nut-ups.md`](./nut-ups.md) — UPS / NUT graceful shutdown
- [`gitops.md`](./gitops.md) — layer boundaries (Flux / Ansible / physical)
