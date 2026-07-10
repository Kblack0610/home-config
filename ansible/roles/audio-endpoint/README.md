# audio-endpoint

Turns an always-on box into a home-cluster audio endpoint for a pair of **Yamaha HS5** powered studio monitors, so the cluster / Home Assistant can drive them for AirPlay, multi-room synced audio, TTS/alerts, and music streaming.

The HS5 is a pair of powered analog monitors: each takes one balanced line input (XLR or 1/4" TRS) plus its own mains. They have no network, USB, or digital input, so the endpoint box bridges network audio to two balanced analog outputs via a USB interface, and joins the cluster as a Home Assistant `media_player`.

## Board-agnostic by design

The signal path uses a **USB balanced interface**, not an I2S HAT, so the host board is just an always-on Linux host with USB + ethernet. Supported equally:

- Raspberry Pi (Raspberry Pi OS)
- Libre Computer board - Le Potato / Sweet Potato / Renegade (Armbian)
- BigTreeTech Pi (Armbian); BigTreeTech CB1 also works but needs a carrier that breaks out USB + ethernet

The board only changes three things, none of which touch this role's logic:

1. The OS image you flash (Raspberry Pi OS vs Armbian) - both Debian-family, so `apt` + `alsa-utils` cover all of them.
2. The ALSA card index - the role autodetects it, and pins the default sink by card *name* so it survives reboots.
3. CB1 only: needs a carrier board for USB + ethernet.

Prefer **wired ethernet** over WiFi for the endpoint - it minimizes clock jitter, which matters at the Snapcast multi-room phase.

## What Phase 0 does

1. Installs `alsa-utils`.
2. Runs `aplay -l` and finds the USB audio card (substring `audio_endpoint_card_hint`, default `USB`).
3. Writes `/etc/asound.conf` making that card the system-default sink (referenced by name, with `type plug` for automatic rate/format conversion).
4. Opt-in: plays a sine tone (left then right) to verify the sink and the L/R cabling into the speakers.

Phase 0 does not install any audio service yet - it gets a verified tone out of the speakers, the foundation everything else sits on.

## Usage

The role is bound in `playbooks/site.yml` to the `audio_endpoints` inventory group, which is empty until you add the box. Adding the host is the only step needed to activate it.

1. Flash + boot the board (Pi OS or Armbian), enable SSH, wire it to ethernet.
2. Plug in the USB interface; cable its two balanced outputs to the HS5 L/R inputs; power both speakers.
3. Add the host under `audio_endpoints` in `ansible/inventory.yml` (and a DHCP `-lan` reservation in `infrastructure/dhcp/devices.yaml`).
4. Dry run, then apply:

```bash
export ANSIBLE_VAULT_PASSWORD_FILE=$HOME/.ansible-vault-pass
ansible-playbook playbooks/site.yml --limit <host> --check --diff
ansible-playbook playbooks/site.yml --limit <host>
```

5. Verify the wiring by ear (plays LEFT then RIGHT):

```bash
ansible-playbook playbooks/site.yml --limit <host> \
  -e audio_endpoint_run_speaker_test=true
```

To pre-stage the OS before the interface is attached, pass `-e audio_endpoint_require_card=false`.

## Variables

See `defaults/main.yml`. Common overrides:

- `audio_endpoint_card_hint` - substring to match in `aplay -l` when the box has more than one USB audio card.
- `audio_endpoint_require_card` - set `false` to converge before the interface is plugged in.
- `audio_endpoint_run_speaker_test` - set `true` per-run to play the verification tone.

## Roadmap (later phases)

- Phase 1 - `shairport-sync` (AirPlay), the quick "usable today" win.
- Phase 2 - Music Assistant as a Flux app (`apps/`): Spotify Connect, radio, local library.
- Phase 3 - wire Music Assistant announce to HA so TTS/alerts duck the music.
- Phase 4 - Snapcast server (Flux) + `snapclient` here; re-point AirPlay/streaming to feed Snapcast for multi-room sync. In the end state `snapclient` owns the card and every other source feeds Snapcast.

Full plan: `~/.claude/plans/yamaha-hs5-cluster-audio-endpoint.md`.
