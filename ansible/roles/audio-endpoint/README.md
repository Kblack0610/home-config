# audio-endpoint

Makes a host one room of the whole-house Snapcast system. The Snapcast server and Music Assistant run in k3s (`apps/snapcast`, `apps/music-assistant`); every room is a `snapclient` of that one server, so all rooms play in sync. The turntable is a Snapcast stream like any other: a box with a USB interface captures its line input and pushes it to the server's Turntable source.

```
Turntable (LINE) -> UFO202 in -> living-room Pi --tcp:4953--> snapserver (k3s)
                                                                  |
             +-----------------------------+----------------------+
             v                             v                      v
   office: desktop snapclient    living-room Pi snapclient   bedroom Pi 3B+ snapclient
   (pulse backend, PipeWire)     (alsa backend, UFO202 out)  (alsa backend, 3.5mm jack)
```

The kitchen is not a host here: its room is the wall tablet's browser client (`apps/home-assistant/config/www/snapcast-tablet.js`). A box whose speaker is on the Pi's onboard 3.5mm jack sets `audio_endpoint_card_hint: "Headphones"` (the bcm2835 card), as `audio-bedroom` does.

Full plan: `~/.claude/plans/and-i-slareyda-have-cryptic-wall.md`.

## Backends

`audio_endpoint_backend` picks how the room plays audio.

- `alsa` (default): a dedicated always-on box (Raspberry Pi OS, or Armbian on a Libre Computer / BigTreeTech board). The role finds the USB interface in `aplay -l`, makes it the default ALSA sink in `/etc/asound.conf`, and configures the packaged system `snapclient.service` (via `/etc/default/snapclient`) to play straight to that card, named by its ALSA id (`plughw:CARD=<id>,DEV=0`) so a USB re-enumeration cannot move it to another card.
- `pulse`: a desktop that already runs PipeWire with `pipewire-pulse`. snapclient runs as a systemd user service (`~/.config/systemd/user/snapclient.service`) with `--player pulse`, so it is one more client on the default sink and mixes with normal desktop audio. It never writes `/etc/asound.conf`, never changes the default sink, and never looks for a USB card, so `audio_endpoint_require_card` does not apply. On Debian, where installing the package enables a system snapclient, the role disables that one.

### Lingering (pulse backend)

The user unit is `WantedBy=default.target`, so it starts with the user's systemd manager, which means at login. The role does not enable lingering. Starting at boot without a login needs `loginctl enable-linger <user>`; on `cachyos-x8664-main` lingering is already on (the `asr-servers` role enables it). Note that before anyone logs in at the seat, PipeWire may not have access to the sound card unless the user is in the `audio` group.

## Snapclient install

- Debian family: `snapclient` from the distro archive (bookworm ships 0.26). The role uses `--host` / `--port`, which every snapclient version accepts; the `tcp://host:port` URL form needs 0.28+.
- Arch / CachyOS: there is no snapcast package in the official repos. When `/usr/bin/snapclient` is missing the role downloads the AUR `snapcast` PKGBUILD, installs the dependencies listed in its own `.SRCINFO`, runs `makepkg` as the login user and installs the result with `pacman -U`. No AUR helper is involved because paru/yay prompt for sudo, which Ansible cannot answer. Later upgrades are ordinary AUR upgrades (`paru -Syu`). In `--check` the build is reported, not run.

## Turntable capture (opt-in)

`audio_endpoint_capture: true` adds `audio-endpoint-capture.service`, a system service that runs

```
arecord -D hw:CARD=<id>,DEV=0 -t raw -f S16_LE -r 48000 -c 2 | nc -N <snapserver> 4953
```

The snapserver's `tcp://0.0.0.0:4953?name=Turntable&mode=server` source accepts one TCP connection and reads raw interleaved PCM in the stream's sample format (`48000:16:2`), with no header, so `arecord | nc` is the whole job: nothing to transcode and no extra daemon. If either side goes away the pipeline ends (nc exits and arecord gets SIGPIPE, or arecord exits and `nc -N` closes the socket), and systemd restarts it after `audio_endpoint_capture_restart_sec` (5 s) with `Restart=always`. It runs as a `DynamicUser` in the `audio` group. The card is the same one the alsa detection finds; the run fails if capture is on and no card matches.

The capture streams silence while the needle is up, so the Turntable stream never reads as idle on the server by itself.

## Usage

The role is bound in `playbooks/site.yml` to the `audio_endpoints` inventory group. The group sets `audio_endpoint_snapserver_host` once for every room; it is required whenever snapclient or capture is on, and the role fails with a clear message if it is empty.

```bash
export ANSIBLE_VAULT_PASSWORD_FILE=$HOME/.ansible-vault-pass
ansible-playbook playbooks/site.yml --limit <host> --tags audio --check --diff
ansible-playbook playbooks/site.yml --limit <host> --tags audio
```

The play does not become root at play level: privileged tasks self-elevate, so the pulse backend talks to the login user's systemd user manager. A real run on a host whose sudo needs a password needs `-K`.

### Office (desktop, pulse backend)

`cachyos-x8664-main` is in `audio_endpoints` with `audio_endpoint_backend: pulse`. It plays through the Scarlett Solo (the PipeWire default sink) into the HS5 pair, alongside desktop audio. Check it after a run with `systemctl --user status snapclient` and `pactl list short sink-inputs`.

### Living room Pi (alsa backend + turntable)

1. Flash and boot the Pi (Pi OS), enable SSH, wire it to ethernet, and give it a DHCP `-lan` reservation in `infrastructure/dhcp/devices.yaml`.
2. Plug in the Behringer UFO202. Turntable RCA into its input (turntable and UFO202 both on LINE), its RCA output into a free analog AUDIO input on the RX-V385.
3. Add the host under `audio_endpoints` in `ansible/inventory.yml`:

   ```yaml
   livingroom-audio:
     ansible_host: 192.168.1.NNN
     audio_endpoint_capture: true
   ```

4. Dry run, then apply (commands above), then check by ear with `-e audio_endpoint_run_speaker_test=true` (plays LEFT then RIGHT). Stop snapclient first (`sudo systemctl stop snapclient`) if it already holds the card, or the tone fails with "device busy".
5. Drop the needle and watch the Turntable stream in snapweb.

The UFO202's card id is `CODEC`; the default hint `USB` matches it. If its input monitor turns out to reach the RCA output, the living room already hears the record directly: then leave the living-room client off the Turntable stream to avoid a doubled signal.

To pre-stage a box before the interface is attached, pass `-e audio_endpoint_require_card=false` (snapclient then plays to `default`; capture still needs the card).

## Variables

See `defaults/main.yml`. Common overrides:

- `audio_endpoint_backend` - `alsa` (default) or `pulse`.
- `audio_endpoint_snapserver_host` / `audio_endpoint_snapserver_port` - snapserver address (required) and stream port (1704).
- `audio_endpoint_snapclient_host_id` - client id in snapweb / Music Assistant / HA (default: the inventory name).
- `audio_endpoint_snapclient_enabled` - `false` for Phase 0 only (ALSA sink, no client).
- `audio_endpoint_capture` / `audio_endpoint_capture_port` - turntable capture and the Turntable source port (4953).
- `audio_endpoint_card_hint` - substring to match in `aplay -l` when the box has more than one USB audio card.
- `audio_endpoint_require_card` - alsa backend only; `false` to converge before the interface is plugged in.
- `audio_endpoint_run_speaker_test` - alsa backend only; `true` per-run to play the verification tone.

## Roadmap

- Phase 0 - USB interface as the default ALSA sink, opt-in test tone. Done in code (#114); not yet run against hardware.
- Snapclient per room - done in code: `alsa` backend for dedicated boxes, `pulse` backend for the office desktop.
- Turntable capture - done in code (`audio_endpoint_capture`); waiting on the living-room Pi and the UFO202.
- Next: living-room Pi in the inventory, Broadlink receiver automation in HA (power on and select the AUDIO input when the living-room group plays), kitchen Pi once speakers are bought.
