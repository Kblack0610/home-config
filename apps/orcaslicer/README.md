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

## Presets

| Printer | Machine | Process | Filament |
|---------|---------|---------|----------|
| Neptune 4 Pro, 0.4 nozzle | `Elegoo Neptune 4 Pro (0.4 nozzle)-OpenNept4une` | `0.20mm Standard ...(0.4 nozzle)-OpenNept4une`, `0.20mm PETG ...` | `Elegoo Generic PLA-OpenNept4une` (210), `Elegoo Generic PETG-OpenNept4une` (240) |
| Neptune 4 Pro, 0.8 nozzle | `Elegoo Neptune 4 Pro (0.8 nozzle)-OpenNept4une` | `0.40mm Standard ...(0.8 nozzle)-OpenNept4une` | same |
| Bambu Lab A1 | `Bambu Lab A1 (0.4 nozzle)-LAN` | system presets | system presets |

Both Neptune machine presets carry the OpenNept4une `PRINT_START ... BED_MESH=adaptive` / `PRINT_END` gcode and firmware retraction. The two Neptune filaments show only for the Neptune (`printer_model` condition).

Every Neptune process names its gcode `{input_filename_base}_{nozzle_diameter[0]}n_{layer_height}mm_{filament_type[0]}_{print_time}.gcode`, for example `Benchy_0.4n_0.2mm_PLA_50m22s.gcode`. The layer height in a name says nothing about the nozzle; to check an old file, read `nozzle_diameter` from `curl -s 'http://192.168.1.54:7125/server/files/metadata?filename=<name>'`.

## Changing a preset

1. Change the file under `profiles/` (or save the preset in a desktop Orca whose `user/default` folders link here) and open a PR.
2. After Flux applies it, restart the app so the init container copies the new files in: `kubectl --context home-k3s -n orcaslicer rollout restart deploy/orcaslicer`. `apps/print-service` reads the ConfigMap mount live.

Every start overwrites the managed presets, so a preset changed only in the browser slicer is lost at the next restart. Presets with other names are left alone. Old strays were moved to `user/default/_retired-2026-11-04/` on the hostPath.

A new preset needs a line in the init container's list in `deployment.yaml` and a `key=path` entry in `kustomization.yaml` (ConfigMap keys cannot hold spaces).

## Verify

```bash
kubectl --context home-k3s -n orcaslicer logs deploy/orcaslicer -c seed-config
kubectl --context home-k3s -n orcaslicer exec deploy/orcaslicer -c orcaslicer -- ls /config/.config/OrcaSlicer/user/default/machine
python3 scripts/check-secret-store.py
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| A merged preset change does not show on `slicer.kblab.me` | The init container copies presets only at pod start | `rollout restart deploy/orcaslicer` |
| A user filament preset is missing from the list, Orca logs `validate_preset: ... can not found preset and inherit in list` | Its `inherits` names a system preset that the Orca version does not ship (the old `Elegoo Generic PLA`) | Point `inherits` at a preset that exists in `/opt/orcaslicer/resources/profiles/Elegoo/` (`Generic PLA @Elegoo`) |
| A gcode printed with the wrong nozzle | The name was read for the nozzle | Read `nozzle_diameter` from Moonraker metadata (above) |
