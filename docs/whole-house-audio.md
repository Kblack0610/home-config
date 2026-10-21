# Whole-house audio

Synced music in every room (Spotify, radio, the turntable) on Snapcast, with Music Assistant as the music app and Home Assistant plus the wall tablets as the remotes. Built 2026-10-12 to 2026-10-15. Per-app detail lives in [apps/snapcast/README.md](../apps/snapcast/README.md), [apps/music-assistant/README.md](../apps/music-assistant/README.md) and [ansible/roles/audio-endpoint/README.md](../ansible/roles/audio-endpoint/README.md); this page is the map.

## How it fits together

```
Spotify / radio --> Music Assistant (k3s) --+
                                            v
Turntable (LINE) -> UFO202 in -> Giada --> Snapcast server (k3s, hp-victus) --> every room, in sync
                                            |
        +-------------------+---------------+--------------------+
        v                   v                                    v
   Living Room          Office                               Kitchen
   Giada -> HDMI ->     desktop snapclient (PipeWire)        wall tablet, browser client
   RX-V385              -> Scarlett Solo -> HS5              in the HA dashboard -> speaker
```

- Snapcast is the speaker network: it takes audio in and plays it in every room in sync.
- Music Assistant is the music app: it feeds what you pick INTO Snapcast. It does not list the Turntable, which goes straight into Snapcast.
- Home Assistant shows each room twice: `media_player.<room>` (Music Assistant: play, browse, group, volume) and `media_player.<room>_snapcast_client` (Snapcast: source picker, which is where Turntable is chosen).

## Rooms

| Room | Snapcast client id | Hardware | Status |
|------|--------------------|----------|--------|
| Living Room | `audio-living` | Giada DN24 (Pi CM4, `audio-living-lan` .120, user `giada`) + Behringer UFO202; synced copy to the RX-V385 over HDMI (`hdmi:CARD=vc4hdmi1`) | live |
| Office | `cachyos-x8664-main` | desktop snapclient as a user service into PipeWire -> Scarlett Solo -> HS5 | live |
| Kitchen | `kitchen` | browser client in the HA dashboard on the Wall Kitchen tablet ([wall-panels.md](wall-panels.md) "Snapcast room on a wall panel") | connects, NOT yet heard - see Open items |

## Records

- Turntable (Audio-Technica AT-LP70X) on LINE -> UFO202 input (LINE). The Giada streams it to the snapserver `Turntable` source (`audio-endpoint-capture.service`, tcp 4953).
- The UFO202 also passes the turntable straight to its RCA output, which sits on an analog input of the receiver: that is "records, living room only, live". With other rooms playing, use the receiver's HDMI input instead (the synced copy), or the rooms echo against each other.
- Pick Turntable for a room on the wall tablet Music page (Source), in snapweb (https://snapcast.kblab.me), or on `media_player.<room>_snapcast_client`.

## Everyday use

- Wall tablets: Home -> Music shows every room with play/pause, volume, browse (Spotify, radio) and Source. The Dashboard button on the photo screen always returns to the full Home view (PR #320).
- Wall and phone Home: the Music section has one tile per room. Tap to add a room to what is playing or drop it out; details in `docs/wall-panels.md` (Cameras and Music on Home).
- Music Assistant: https://music.kblab.me. Snapcast provider points at the external server (`snapserver-direct.snapcast.svc.cluster.local:1705`); Radio Browser and Spotify are added. Jellyfin has no music files, so it is not added.
- Snapweb: https://snapcast.kblab.me for per-room stream and volume.

## Gotchas

- The Giada does not come back from a software reboot (2026-10-15), and after the 2026-10-20 power cut it hung with its power LED on but nothing on the LAN for 16 hours. Unplugging and replugging its power brought it straight back. Never reboot it remotely. Since then its hardware watchdog is armed (`ansible/roles/hw-watchdog`, also on audio-bedroom) and its kernel log streams to pi5-master (`ansible/roles/crash-evidence`), so the next hang either resets itself or leaves evidence. `NodeDown` pages ntfy `homelab-alerts` within 2 minutes of it dropping.
- Snapserver sends `pcm`, not `flac` (PR #322): the kitchen browser client played only silence with flac. snapclient handles pcm natively; it costs about 1.5 Mbit/s per room.
- A snapserver restart drops Music Assistant's stream; press play again. Clients reconnect on their own within about a minute.
- HA's Snapcast integration once lost all its entities for two days until an HA restart (2026-10-13 to 10-15, cause unknown). If the Source pickers vanish, reload the Snapcast integration.
- The kitchen tablet has one USB-C port. A USB-C audio dongle in it blocks charging and levers the port; use a dongle with PD pass-through, or a Pi Zero for the kitchen speaker.
- The kitchen client logs to HA's system log (logger `snapcast_tablet`): load, socket, codec, AudioContext state and per-10 s playback stats (`audible` = non-silent buffers, `hardSyncs` = sync jumps, each one an audible click, `maxAgeMs` = worst drift from the server clock in that window). That is how to debug a silent panel without devtools.

## Backups

Music Assistant settings (PR #307) and snapserver `server.json` (PR #308) back up nightly to the node and the NAS ([backup-runbook.md](backup-runbook.md)). Everything else is in this repo.

## Open items

- Kitchen: no sound heard yet. After the pcm switch the client is connected; the test was interrupted because the kitchen tablet's USB-C port was damaged by the audio dongle and the tablet would not charge. Next: get the tablet charging (port repair if needed), then play to Kitchen and read the `snapcast_tablet` stats. Longer term, prefer a Pi Zero 2 W driving the kitchen speaker so the tablet is only a touchscreen.
- Receiver auto-on and input switching: try HDMI-CEC from the Giada first; the Broadlink IR package (`apps/home-assistant/config/packages/avr_yamaha.yaml`) is the fallback and still has two placeholders.
- TV audio to other rooms: needs a second capture device on the Giada (TV optical -> optical-to-RCA converter -> second UFO202/UCA202, or a Linux-confirmed USB optical-in adapter) and a second capture service.
- Wall tablets show all rooms on the Music page rather than their own room first; the Voice Satellite room assignment could drive that.
- Remove the kitchen client's diagnostics once the kitchen plays.
