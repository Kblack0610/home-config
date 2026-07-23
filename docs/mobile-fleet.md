# Mobile fleet - onboarding and monitoring phones/tablets

How a phone or tablet joins the fleet and shows up on the liveness board at `fleet.kblab.me`, next to the computers and k3s nodes. This is the mobile companion to [`device-fabric.md`](./device-fabric.md) (the whole-fleet model) and [`headscale-setup.md`](./headscale-setup.md) (tailnet join).

The point: a mobile device should enroll like any other machine - one command - instead of a hand-typed runbook. We already have every building block (gatus-fleet board, Headscale tailnet, the `fleet-pulse` heartbeat pusher, Headwind MDM, `headwind-fleet-bridge`); this doc wires them together for mobile.

## Two lanes, one board

Both lanes surface on the same gatus group (`android`) on `fleet.kblab.me`. The only difference is WHO pushes the heartbeat. Pick the lane by the device's ROLE, not by its hardware.

| Lane | Devices | Enroll mechanism | Who pushes liveness |
|------|---------|------------------|---------------------|
| A - light touch | carry phones, any device you use normally | Tailscale (Headscale) join + `fleet-pulse` heartbeat from Termux (Termux:Boot + cron) | the device itself |
| B - full MDM | dedicated / kiosk / spare tablets | Headwind device-owner QR enroll (factory reset -> QR) | `headwind-fleet-bridge` reads the Headwind DB and pushes on the device's behalf |

Lane B is already in production for the 3 wall tablets (`h0001`-`h0003`). Lane A is what makes a personal daily-driver phone fleet-visible WITHOUT handing the whole phone to an MDM.

### Why not just MDM everything

Headwind Community enrollment is device-owner: it requires a factory reset and then effectively owns the device, and `mdm.kblab.me` is LAN/tailnet-only. That is correct for a wall tablet or a dedicated spare, and far too heavy for a phone you carry. Lane A trades the MDM's silent app-push for keeping your phone yours - the cost is a small one-time manual app install (below).

## The roster rule (do not skip this)

A carry phone is declared as a gatus `external-endpoints` entry (so it shows on the web board with a last-seen timestamp) but is deliberately kept OUT of `FLEET_ROSTER` on the desktop status bars.

Reason: a phone sleeps. Android Doze pauses cron, so the heartbeat stops whenever the screen is off for a while. If the phone were in `FLEET_ROSTER`, the desktop "is my whole fleet alive" glyph would read amber every time your phone slept - which is always. This is the same reasoning `apps/gatus-fleet/config.yaml` already applies to the sleeping iot Pi Zeros. Phones get last-seen semantics on the board, not glyph-gating on your desktop.

## Lane A - onboard a carry phone

### One-time manual prefix (cannot be automated without device-owner MDM)

1. Install from F-Droid (Termux and Termux:Boot MUST come from the same source - the Play Store builds are deprecated and the add-on will not talk across sources): Termux, Termux:Boot.
2. Install Tailscale (Play Store or F-Droid). Open it -> Use an alternate server -> `https://headscale.kblab.me` -> connect. Approve on the desktop with `headscale nodes register` (see `headscale-setup.md`).

### One command (everything after this is automated)

In Termux (download-then-run so the prompts have a terminal):

```
curl -fsSLO https://raw.githubusercontent.com/Kblack0610/.dotfiles/main/.local/bin/termux-fleet-onboard
FLEET_GATUS=https://fleet.kblab.me bash termux-fleet-onboard
```

`termux-fleet-onboard` (public dotfiles, host-generic - the fleet URL comes from `FLEET_GATUS`) chains three opt-in pieces:
- **fleet heartbeat** via `fleet-pulse` enroll (Termux branch): probes the gatus key first (never installs a silently-failing agent), writes `~/.config/fleet-pulse/{env,token}`, drops `~/.termux/boot/start-fleet-pulse.sh` (starts `crond` at boot), and adds a crontab line that runs `push.sh` every 15 minutes.
- **sshd over the tailnet** (opt-in, recommended): Termux `sshd` on port 8022, key-based, started at boot. Gives headless control of the phone from any fleet machine - see below.
- **notes-on-phone** (opt-in): delegates to `notes-termux-bootstrap`.

You paste the shared fleet token once at a hidden prompt (grab it from rbw, or decrypt `apps/gatus-fleet/fleet-token-secret.sops.yaml` on the desktop). It is never passed on the command line (the wrapper reads prompts from `/dev/tty` and hands the token to `enroll.sh` via a temp file).

### SSH into the phone (Termux sshd over the tailnet)

Because the phone is on the tailnet, you can SSH into it headlessly - no port-forwarding, no public exposure. `termux-fleet-onboard` sets this up when you answer yes: it installs `openssh`, appends your desktop public key to `~/.ssh/authorized_keys`, starts `sshd` (Termux's sshd listens on **port 8022**), and adds a `~/.termux/boot/start-sshd.sh` so it comes back after a reboot. Connect from any fleet machine:

```
ssh -p 8022 <phone-tailnet-ip>      # tailscale ip -4 on the phone shows it
```

Key auth is preferred; if you skip pasting a key, set a password on the phone with `passwd`. This is the "a lot of control" lane: run commands, push files, drive the phone from your desktop.

### Declare the device (one-time, done on the desktop before the phone enrolls)

Add one `external-endpoints` entry under the `android` group in `apps/gatus-fleet/config.yaml`:

```yaml
- name: <kebab-handle>   # e.g. ken-s25-main - Tier-A push heartbeat (not Headwind)
  group: android
  token: "${FLEET_TOKEN}"
```

Commit -> push to forgejo -> Flux reconciles gatus-fleet. Until the key exists, the phone's first push 404s (fleet-pulse probe-first will refuse to install), so declare it first.

Do NOT add the phone's name to `FLEET_ROSTER` (see the roster rule above).

## Lane B - onboard a dedicated/kiosk device

Unchanged from `device-fabric.md`: factory reset -> tap 7x -> scan the Headwind enrollment QR from `mdm.kblab.me` -> the agent installs as device owner. `headwind-fleet-bridge` then reads Headwind's `devices` table every 60s and pushes a heartbeat per device to `android_<slug>` on gatus-fleet. No client-side heartbeat needed. Kiosk lockdown comes from FreeKiosk (see `scripts/provision-wall-tablet.sh`), not Headwind Community.

## Verifying a phone is on the fleet

From the desktop, simulate/confirm the key (replace name and token):

```
FLEET_NAME=<handle> FLEET_GROUP=android GATUS_BASE=https://fleet.kblab.me \
  FLEET_TOKEN_FILE=<file-with-token> ~/.dotfiles/.local/src/fleet-pulse/push.sh
```

Expect `HTTP 200`. A `404` means the key is not declared in `apps/gatus-fleet/config.yaml` (or the name/group is wrong). Then check the board at `fleet.kblab.me`, or `GET /api/v1/endpoints/statuses` filtered to `android_<handle>`.

## Follow-ups

- `docs/device-fabric.md` and the project anchor still describe a single `fleet` gatus group; the fleet has since split into `fleet.kblab.me` (`apps/gatus-fleet/`) with per-type groups (`homelab`, `workplace`, `k3s`, `android`, `iot`). Pre-existing drift, tracked separately.
- If the `android` group gets crowded, split carry phones into their own `mobile` group.
