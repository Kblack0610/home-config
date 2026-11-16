# OrcaSlicer

OrcaSlicer in the browser at `https://slicer.kblab.me` (linuxserver image, Selkies), running on asus-laptop with its config on the hostPath `/mnt/media/app-config/orcaslicer` and the NAS share `/mnt/nas/public/3d-printing` mounted at `/prints`. It slices for the Elegoo Neptune 4 Pro (see `apps/neptune/README.md`) and the Bambu Lab A1. `apps/print-service` runs the same image headless and reads the same presets.

## Files

| File | Purpose |
|------|---------|
| `profiles/{machine,filament,process}/` | The user presets, named exactly as OrcaSlicer names them. The one source for every Orca |
| `OrcaSlicer.conf` | First-run app config for a fresh k3s install (wizard done, vendors and nozzle variants enabled) |
| `kustomization.yaml` | Builds the `orcaslicer-profiles` ConfigMap from the two above (fixed name, `apps/print-service` mounts it) |
| `deployment.yaml` | The app; its `seed-config` init container copies the presets in on every start |
| `bambu-a1-secret.yaml` | SOPS: the A1 LAN access code and serial, filled into the `@A1_...@` placeholders at start |
| `check-presets.sh` | Updating step 1: proves every `inherits` resolves on an image tag, in a throwaway container |

## Presets

| Printer | Machine | Process | Filament |
|---------|---------|---------|----------|
| Neptune 4 Pro, 0.4 nozzle | `Elegoo Neptune 4 Pro (0.4 nozzle)-OpenNept4une` | `0.20mm Standard ...(0.4 nozzle)-OpenNept4une`, `0.20mm PETG ...` | `Elegoo Generic PLA-OpenNept4une` (210), `Elegoo Generic PETG-OpenNept4une` (240) |
| Neptune 4 Pro, 0.8 nozzle | `Elegoo Neptune 4 Pro (0.8 nozzle)-OpenNept4une` | `0.40mm Standard ...(0.8 nozzle)-OpenNept4une` | same |
| Bambu Lab A1 | `Bambu Lab A1 (0.4 nozzle)-LAN` | system presets | system presets |

Both Neptune machine presets carry the OpenNept4une `PRINT_START ... BED_MESH=adaptive` / `PRINT_END` gcode and firmware retraction. The two Neptune filaments show only for the Neptune (`printer_model` condition).

Every Neptune process names its gcode `{input_filename_base}_{nozzle_diameter[0]}n_{layer_height}mm_{filament_type[0]}_{print_time}.gcode`, for example `Benchy_0.4n_0.2mm_PLA_50m22s.gcode`. The layer height in a name says nothing about the nozzle; to check an old file, read `nozzle_diameter` from `curl -s 'http://192.168.1.54:7125/server/files/metadata?filename=<name>'`.

## Changing a preset

1. Change the file under `profiles/` and open a PR. Keep the `@A1_...@` placeholders in the A1 machine preset; the real code is filled from `bambu-a1-secret.yaml` at start.
2. After Flux applies it, restart the app so the init container copies the new files in: `kubectl --context home-k3s -n orcaslicer rollout restart deploy/orcaslicer`. `apps/print-service` reads the ConfigMap mount live.

Every start overwrites the managed presets, so a preset changed only in the browser slicer is lost at the next restart. Presets with other names are left alone. Old strays were moved to `user/default/_retired-2026-11-04/` on the hostPath.

A new preset needs a line in the init container's list in `deployment.yaml` and a `key=path` entry in `kustomization.yaml` (ConfigMap keys cannot hold spaces).

## Updating

The app and `apps/print-service` pin the same linuxserver tag (now `v2.5.0-ls49`); bump both in one PR. Renovate opens that PR (`renovate/orcaslicer`, docs/renovate.md); before merging it:

1. Prove every `inherits` in `profiles/` still resolves: `apps/orcaslicer/check-presets.sh <tag>` (needs docker; pulls the image). Since 2.5.0 each vendor is an opaque `<vendor>.opc` (not zip) and `/opt/orcaslicer/resources/profiles/Elegoo/` holds no preset files, so `ls` over the image proves nothing. The script copies the vendor bundles into a throwaway datadir `system/`, puts `profiles/` in `user/default/`, and slices a cube with each machine/process/filament set with the image's own CLI (the A1 machine is resolved with `--export-settings`); a preset with a bogus `inherits` is the negative control and must fail. OrcaSlicer 2.4 renamed the Elegoo presets (`Elegoo Neptune 4 Pro (0.4 nozzle)` -> `Elegoo Neptune 4 Pro 0.4 nozzle`, `@Elegoo Neptune4Pro (0.4 nozzle)` -> `@Elegoo N4Pro 0.4 nozzle`); the GUI maps old names through `renamed_from`, the CLI does not. Fix a red by pointing `inherits` at the new name (the new names are in `apps/print-service/server.py`).
2. Re-run the print-service slice check (`apps/print-service/README.md`, "Test / verify").
3. Slice once in the GUI with each process preset in `profiles/process/`. print-service only uses the system process presets, so it cannot catch a value the new version rejects (2.4.2 refused `small_perimeter_speed: "0"`; the check script slices a cube with them, which does not exercise every feature of each process preset).

## Verify

```bash
kubectl --context home-k3s -n orcaslicer logs deploy/orcaslicer -c seed-config
kubectl --context home-k3s -n orcaslicer exec deploy/orcaslicer -c orcaslicer -- ls /config/.config/OrcaSlicer/user/default/machine
python3 scripts/check-secret-store.py
```

## Stream and smoothness

Checked 2026-11-11 on `v2.5.0-ls49`; nothing was changed. Selkies 2.2.1 streams over websockets from a Wayland session (labwc). The Radeon dGPU renders (`Renderer: GL on /dev/dri/renderD128` in the pod log) and encodes H.264 with VAAPI (`Render node 0 encodes H264, H265 on vaapi`). Idle, the pod uses about 11m CPU and 825 MiB.

Two settings cap sharpness and smoothness, both from `3dba36e` (2026-04), when the CPU still encoded:

- `SELKIES_IS_MANUAL_RESOLUTION_MODE=true` at 1280x720: the stream is stretched to the browser window, so it looks soft on a big monitor. To fix it, drop the three manual-resolution vars so the remote desktop takes the window size. Check a phone first, because the Orca UI may be too cramped at phone size; if it is, set manual mode to 1920x1080 instead.
- `SELKIES_FRAMERATE=30`: 60 makes rotating the plate smoother, at the cost of more bandwidth through the public tunnel off the LAN. Try 45 if it stutters away from home.

After a change, the Selkies start line in `kubectl --context home-k3s -n orcaslicer logs deploy/orcaslicer -c orcaslicer | grep "Selkies .* starting"` shows the resolution and framerate. Today it reads `encoder h264enc at 8-240 fps, fixed 1280x720`.

For an app window with no tabs, use Chromium's "Install" button in the address bar (Selkies ships a `manifest.json`) or `chromium --app=https://slicer.kblab.me`.

Selkies stays as the wrapper. Moonlight/Sunshine needs a client install and has no browser path, Selkies WebRTC mode needs a TURN server off the LAN (the tunnel carries only HTTP and websockets), and KasmVNC, noVNC and Guacamole are older and slower.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| A merged preset change does not show on `slicer.kblab.me` | The init container copies presets only at pod start | `rollout restart deploy/orcaslicer` |
| A user filament preset is missing from the list, Orca logs `validate_preset: ... can not found preset and inherit in list` | Its `inherits` names a system preset that the Orca version does not ship (the old `Elegoo Generic PLA`) | Point `inherits` at a preset the version ships (`Generic PLA @Elegoo`); `check-presets.sh <tag>` shows which names resolve |
| Slicing fails with "Invalid parameter value(s)" (the CLI returns -18) | A preset key holds a value the Orca version no longer accepts | Find the key: apply the preset keys one at a time onto the system parent and slice with `/usr/bin/orca-slicer --slice 0`. Drop the key so it inherits, or set a valid value |
| A gcode printed with the wrong nozzle | The name was read for the nozzle | Read `nozzle_diameter` from Moonraker metadata (above) |
| The slicer went black or restarted on its own | On the X11 images (2.3.2 and earlier) the Xvfb server segfaulted: `(EE) Caught signal 11` in Loki on 2026-10-23 and 2026-11-04. The current images use Wayland, and there were no restarts from 2026-11-04 to 11-11 | Search Loki for `{namespace="orcaslicer",container="orcaslicer"} \|~ "(?i)segfault\|signal 11"`. If it is still crashing, add a Sentinel `kubectl` watch with `expect_restarts_max` on `home-k3s/orcaslicer/app.kubernetes.io/name=orcaslicer` |
