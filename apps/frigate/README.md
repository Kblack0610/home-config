# Frigate

Frigate NVR on home-k3s, reconciled by Flux. Object detection runs on hp-victus's GPU (RTX 4060 Laptop) through the ONNX detector in the `-tensorrt` image, and video decode uses NVDEC. Events go to Home Assistant over MQTT (`apps/mosquitto`) and the Frigate integration.

## Files

| File | Purpose |
|------|---------|
| `config.yml` | Frigate config, copied over `/config/config.yml` on every pod start. Git wins: edits made in the Frigate UI config editor are lost on restart unless brought back here. |
| `deployment.yaml` | One replica pinned to hp-victus, `runtimeClassName: nvidia`, one GPU. Init containers seed the config and fetch the model. |
| `model/build.sh` | Builds and publishes the YOLOv9 ONNX model (see Model below). |
| `service.yaml` | ClusterIP for the UI (8971), the internal API (5000) and the go2rtc restream (8554); a LoadBalancer for WebRTC (8555). |
| `ingress.yaml` | `frigate.kblab.me`, LAN only (external-dns skips it). |
| `pvc.yaml` | `frigate-config` (db, model cache) and `frigate-media` (recordings), both local-path on hp-victus. |
| `camera-secret.yaml` | SOPS. amcrest-ptz-1's `frigate` account as `FRIGATE_RTSP_USER` / `FRIGATE_RTSP_PASSWORD`, amcrest-living-room's `admin` account as `FRIGATE_LIVING_ROOM_USER` / `FRIGATE_LIVING_ROOM_PASSWORD`. |
| `backup-cronjob.yaml` | Daily `sqlite3 .backup` of `frigate.db` + `config.yml` to the NAS. Recordings are not backed up. |

## Access

- UI: `https://frigate.kblab.me` (LAN only). Log in as `admin`; the password is in rbw as `frigate_admin`. If it is lost, set `auth.reset_admin_password: true` in `config.yml`, roll the pod, read the new password from the log, and set it back to false.
- Home Assistant: the Frigate integration, URL `http://frigate.frigate.svc.cluster.local:5000`. Port 5000 has no authentication, which is why it is ClusterIP only.

## Storage

local-path does not enforce PVC sizes, so `frigate-media` can grow until the hp-victus disk is full. Frigate's cleanup only steps in when free space on that whole filesystem gets low. Retention in `config.yml` is the real cap: motion 3 days, alerts 14, detections 7, no continuous recording. hp-victus had 169G free (82% used) on 2026-10-17. Check before adding cameras or raising retention:

```bash
kubectl --context home-k3s get --raw /api/v1/nodes/hp-victus/proxy/stats/summary | jq '.node.fs | {availableGB: (.availableBytes/1e9), capacityGB: (.capacityBytes/1e9)}'
```

## Model

Frigate 0.18 ships no GPU model. `model/build.sh` exports YOLOv9-s at 320x320 with the recipe from Frigate's docs (pinned yolov9 commit, classic docker builder) and publishes it to the Forgejo generic registry. The `fetch-model` init container downloads it into `/config/model_cache/` and checks `MODEL_SHA256`, re-fetching on mismatch and failing the pod if the hash still does not match.

To change the model: run `model/build.sh <size> <imgsize>`, publish with `FORGEJO_TOKEN=... model/build.sh --publish <file>`, then update `MODEL_URL` / `MODEL_SHA256` in `deployment.yaml` and `model.path` / `width` / `height` in `config.yml` together.

Moving off the GPU later (a Pi with a Hailo-8L or Coral): change the `detectors` + `model` blocks in `config.yml`, the image tag, and the `nodeSelector` / `runtimeClassName` / GPU limit in `deployment.yaml`.

## Cameras

### amcrest-ptz-1 (Amcrest IP4M-1041B)

4MP indoor pan/tilt with two-way audio. 192.168.1.80, `amcrest-ptz-1.lan`, MAC 9C:8E:CD:32:6D:0B (DHCP reservation in `infrastructure/dhcp/devices.yaml`). Firmware V2.800.1ENN001.0.R (2024-10-29), the build Amcrest publishes for both the IP4M-1041 and the IP2M-841W-V3.

Accounts, both in rbw:

| rbw item | Camera account | Used by |
|----------|----------------|---------|
| `amcrest_admin` | `admin` | Camera web UI and setup |
| `amcrest_frigate` | `frigate` (group `user`; ONVIF Operator) | Frigate RTSP + ONVIF PTZ, via `camera-secret.yaml` |
| `amcrest_hass` | `hass` (group `admin`, no ONVIF) | Home Assistant's Amcrest integration (privacy mode, day/night, motion and audio toggles, presets), via `apps/home-assistant/amcrest-camera-secret.yaml` and `config/packages/amcrest.yaml` |

This firmware's HTTP API checks the password but not the account's permissions: the `frigate` user can read and change settings even though its group grants only live view and playback, and `modifyGroup` returns Not Implemented. Treat every account on the camera as an admin. The protection is network-level: keep the camera on the LAN with no port forward.

Camera settings Frigate depends on. A firmware upgrade restores defaults (the upgrade package ends with `IPC_RestoreDefault`), so reapply them after one:

| Setting | Value | Why |
|---------|-------|-----|
| Main stream | H.264, 2560x1440, 15 fps, GOP 15, Smart Codec off | Browsers play H.264; a keyframe every second keeps stream start and recording segment boundaries tight (default GOP 60 at 30 fps) |
| Sub stream | H.264, 640x480, 5 fps, GOP 5 | Detection input; matches `detect` in `config.yml` |
| Audio | AAC (default) | Recordings keep audio with no transcode |
| Time | NTP `pool.ntp.org`, `NTP.TimeZone=28` (UTC-08:00), `Locales.DSTEnable=false` | DST must stay OFF, see ONVIF below. The router does not answer NTP. `NTP.TimeZone` is an offset index, not the web UI's label list |
| On-screen clock | `VideoWidget[0].TimeTitle.EncodeBlend=false` | With DST off the camera's local clock is an hour behind in summer; better no timestamp in the video than a wrong one. Frigate's timeline shows the real time |
| Password | Letters and digits only | Special characters break RTSP URLs |

### ONVIF (PTZ)

Two traps, both found on 2026-10-19, both of which surface as the same misleading fault, `Sender not Authorized. Invalid username or password! You still have N attempt(s)`:

1. ONVIF has its own user store. Accounts and passwords set through the CGI or web API (`userManager.cgi`) do not reach it: after the admin password was changed over CGI, ONVIF still accepted the factory `admin`/`admin`, and `frigate` did not exist for ONVIF at all. Manage ONVIF users with ONVIF itself (`GetUsers`, `CreateUsers`, `SetUser`, e.g. via `onvif-zeep-async` inside the Frigate pod). Current ONVIF users: `admin` (Administrator) and `frigate` (Operator, enough for PTZ), with the same passwords as their rbw items. After any factory reset or firmware upgrade, check that `admin`/`admin` is refused over ONVIF.
2. With the camera's DST enabled, its ONVIF layer reports UTC as local + 8 and ignores the DST hour, so in summer its UTC runs an hour fast and every WS-Security timestamp is rejected. Keep `Locales.DSTEnable=false`. `GetSystemDateAndTime` needs no auth; compare its `UTCDateTime` with `date -u`.

Failed ONVIF logins lock the account for ONVIF only (`frigate` showed "0 attempts" while RTSP kept working). Test with one call, not a loop.

### Settings over the API

Settings are applied over the camera's CGI API with digest auth. Pass `curl -g`: the setting names contain `[0]`, which curl otherwise treats as a glob and silently drops the request.

```bash
PW=$(rbw get amcrest_admin)
curl -g --digest -u "admin:$PW" 'http://192.168.1.80/cgi-bin/configManager.cgi?action=setConfig&Encode[0].MainFormat[0].Video.FPS=15&Encode[0].MainFormat[0].Video.GOP=15'
```

Check the streams before blaming Frigate:

```bash
ffprobe -v error -rtsp_transport tcp -show_entries stream=codec_name,width,height,avg_frame_rate -of compact \
  "rtsp://frigate:$(rbw get amcrest_frigate)@192.168.1.80:554/cam/realmonitor?channel=1&subtype=0"
```

### amcrest-living-room (Amcrest ASH21-B-V2)

1080p indoor camera with a fixed lens, on Wi-Fi (`BrownDooDoo`). 192.168.1.81, MAC 24:14:07:A2:B7:2A (the Wi-Fi interface; its Ethernet MAC 9C:8E:CD:38:AE:85 has no reservation). Firmware 2.680.00AC008.0.R (2022-04-06). Set up on 2026-10-27.

This is Amcrest's "Smart Home" line, a different product from the IP4M. It has no local web UI and no CGI API: port 80 accepts a connection and then sends an empty reply, and only RTSP and ONVIF work. The IP4M recipes above (CGI settings, `curl -g`, the `hass` account and the Home Assistant Amcrest integration) do not apply. Its first password can only be set through the Amcrest Smart Home app.

| rbw item | Camera account | Used by |
|----------|----------------|---------|
| `amcrest-wifi-camera-password` | `admin` (the device password set in the app) | Frigate RTSP, via `FRIGATE_LIVING_ROOM_*` in `camera-secret.yaml`; ONVIF |

Frigate uses `admin` because RTSP knows no other account. ONVIF has its own user store here too: a `frigate` user created with ONVIF `CreateUsers` was refused over RTSP (401), so it was deleted again. ONVIF `admin` shares the app password and `admin`/`admin` is refused.

Stream settings are applied over ONVIF Media2 (`/onvif/media2_service`, `SetVideoEncoderConfiguration`). Media1 reports the main stream as H264 even when it is H.265, so read and write it through Media2. The app's defaults were H.265 main at 30 fps, GOP 60. A reset restores them:

| Setting | Value | Why |
|---------|-------|-----|
| Main stream (`VideoEncoder000`) | H.264, 1920x1080, 15 fps, GOP 30, 2048 kbps | Browsers play H.264. GOP 30 is the camera's minimum (range 30-150), a keyframe every 2 s |
| Sub stream (`VideoEncoder001`) | H.264, 640x480, 5 fps, GOP 30, 512 kbps | Detection input; matches `detect` in `config.yml` |
| Audio | AAC (default) | Recordings keep audio with no transcode |
| Time | DST on, TZ GMT-06:00 (app defaults) | ONVIF UTC matched `date -u` with these, so the DST bug from the IP4M does not show here. The on-screen clock can only be changed in the app |

ONVIF `SystemReboot` restarts it, which is how it was moved onto its DHCP reservation without a power cycle.

After a factory reset (hold the pinhole a full 30 s; a short press only reboots it):

1. Check it really reset: the unauthenticated WS-Discovery reply (UDP 3702) reports the init state, which reads uninitialized after a real reset.
2. Add it in the Amcrest Smart Home app on `BrownDooDoo`. When the app asks for the device password, use `amcrest-wifi-camera-password` from rbw. The app's other password prompt is the Amcrest cloud login, not the camera's.
3. Check `admin`/`admin` is refused over ONVIF, with one call.
4. Reapply the stream settings above over Media2 and confirm with `ffprobe`.

## Verify

```bash
kubectl --context home-k3s -n frigate get pods -o wide
kubectl --context home-k3s -n frigate logs deploy/frigate --tail=100
kubectl --context home-k3s -n frigate exec deploy/frigate -- curl -s localhost:5000/api/stats | jq '.detectors, .cameras'
```
