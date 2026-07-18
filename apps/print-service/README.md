# print-service — the "print" capability backend

A small, dependency-free HTTP API that turns an STL on the NAS into gcode and
dispatches it to a printer. It is the first capability on the orchestration
"action bus" (voice/chat/agent -> orchestrator -> capability). The orchestrator,
a `print` CLI, and a `print-mcp` MCP server are all thin clients of this service.

- Namespace: `orcaslicer` (co-located with the OrcaSlicer GUI so it reuses the
  `orcaslicer-profiles` ConfigMap and the NAS `3d-printing` hostPath).
- Image: `lscr.io/linuxserver/orcaslicer:v2.3.2-ls9` — used only for the
  `orca-slicer` CLI binary and its bundled `python3`; the Selkies desktop
  entrypoint is bypassed (`command: python3 /app/server.py`).
- Slicing is CPU-only, so no GPU / no `privileged` (unlike the GUI).
- `server.py` is stdlib-only (no runtime pip), shipped as a hashed ConfigMap so
  an edit rolls the Deployment.

## API

| Method | Path | Body / query | Does |
|--------|------|--------------|------|
| GET  | `/healthz` | - | liveness |
| GET  | `/printers` | - | known printers + `processes` + `filaments` |
| POST | `/slice` | `{file, printer?, filament?, process?}` | slice an STL under `/prints` to gcode on the NAS |
| POST | `/print` | `{file, printer?, filament?, process?, start?}` | slice -> upload to Moonraker -> (default) start |
| GET  | `/status` | `?printer=neptune` | Moonraker `print_stats`/`webhooks`/`display_status` |
| POST | `/cancel` | `{printer}` | Moonraker cancel |

`file` is a filename (or subpath) under the NAS `3d-printing` share; path
traversal is rejected. Defaults: `printer=neptune`, `filament=pla`,
`process=standard`, `start=true`.

Printers: **neptune** (Elegoo Neptune 4 Pro, via Moonraker). Bambu A1 is a
documented follow-up (it uses FTP+MQTT via ha-bambulab, not Moonraker) and is
intentionally not in the dispatch path yet.

Options today: `process` = `standard` (0.20mm) | `fine` (0.12mm) | `draft`
(0.24mm); `filament` = `pla` | `petg`.

## Why the graft (the load-bearing detail)

OrcaSlicer's CLI (`orca-slicer --load-settings machine.json;process.json
--load-filaments f.json --slice 0 --outputdir DIR file.stl`) has two gotchas we
hit and solved (verified 2026-07-17, OrcaSlicer 2.3.2):

1. **User presets fail a compatibility check.** Passing the seeded
   `-OpenNept4une` user presets directly returns `The selected printer is not
   compatible with the process preset`. The child's `compatible_printers`
   override is *not* honored (the inherited parent's list wins), and forcing it
   did not work. So the service loads the **system** Neptune-4-Pro machine /
   process / filament (guaranteed compatible, they carry a `type` field) and
   **grafts only the machine** overrides from the seeded `machine-neptune4pro.json`
   onto the system machine, skipping identity keys (`name`, `inherits`, `from`,
   `type`, `*_settings_id`). That preserves the critical OpenNept4une
   `PRINT_START ... BED_MESH=adaptive` / `PRINT_END` gcode while keeping the
   compatible system identity.
2. **Do not graft process/filament.** Grafting the user process/filament onto the
   2.3.2 base returns `Invalid parameter value(s) in the 3mf` (the seed files
   carry keys/values the system base rejects). Process = layer height
   (material-agnostic); material is chosen purely via the system filament preset
   (`Generic PLA @Elegoo` vs `Generic PETG @Elegoo`, which sets the right
   nozzle/bed temps — PLA 210, PETG 240, verified).

Runs headless with `QT_QPA_PLATFORM=offscreen` and no `DISPLAY` (no X server).
`--slice 0` emits `plate_1.gcode`, which the service renames to `<stl>.gcode` on
the NAS.

## Test / verify

Slice path (works even when the printer is off):

```bash
kubectl -n orcaslicer port-forward svc/print-service 8080:8080 &
curl -s localhost:8080/printers | jq
curl -s -XPOST localhost:8080/slice \
  -d '{"file":"corner_lock_lower.stl","filament":"pla","process":"standard"}' | jq
# -> {"gcode":"corner_lock_lower.gcode", ...}; gcode appears on the NAS 3d-printing share
```

Dispatch path (**requires the Neptune powered on** — Moonraker at
`neptune.neptune.svc.cluster.local:7125`):

```bash
curl -s localhost:8080/status?printer=neptune | jq        # printer state
curl -s -XPOST localhost:8080/print -d '{"file":"corner_lock_lower.stl"}' | jq
# -> slices, uploads to Moonraker, starts; watch HA sensor.neptune_print_state
```

## Deploy

GitOps only: edit -> commit -> push to **forgejo** -> Flux reconciles (never
`kubectl apply`). Registered in `apps/kustomization.yaml`.

## Follow-ups

- **Live Moonraker dispatch test** once the Neptune is powered on (upload +
  print/start + cancel).
- **`print` CLI + `print-mcp` MCP server** as thin clients (the CLI becomes an
  agent skill; the MCP is the scoped, secure surface for voice).
- **Bambu A1** dispatch via ha-bambulab (FTP + MQTT print-project).
- **Upload-from-request** (accept an STL in the POST body instead of a NAS
  filename) and a small durable job/queue once the orchestrator drives it.
- **More materials / nozzles** as needed (extend the `processes`/`filaments`
  maps).
