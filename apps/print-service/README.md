# print-service — the "print" capability backend

A small, dependency-free HTTP API that turns an STL on the NAS into gcode and
dispatches it to a printer. It is the first capability on the orchestration
"action bus" (voice/chat/agent -> orchestrator -> capability). The orchestrator,
a `print` CLI, and a `print-mcp` MCP server are all thin clients of this service.

- Namespace: `orcaslicer` (co-located with the OrcaSlicer GUI so it reuses the
  `orcaslicer-profiles` ConfigMap and the NAS `3d-printing` hostPath).
- Image: `lscr.io/linuxserver/orcaslicer:v2.5.0-ls49`, the same tag as
  `apps/orcaslicer` (bump both together) — used only for the
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

## Why the datadir (the load-bearing detail)

OrcaSlicer 2.5 ships each vendor as an opaque `<vendor>.opc` bundle, so the system
preset files the service used to read (and graft the machine overrides onto) are gone.
The CLI (`orca-slicer --datadir D --load-settings machine.json;process.json
--load-filaments f.json --slice 0 --outputdir DIR file.stl`) resolves a user preset's
`inherits` only when:

1. the vendor bundle is in `D/system/` (the service copies `Elegoo.opc` there), and
2. the preset sits in `D/user/default/<type>/`, its file name equals its `name`, and it
   has a `version` key (without one it is skipped: "Preset was not found in the loaded bundle").

So each slice builds a throwaway datadir holding the seeded OpenNept4une machine preset
(it carries `PRINT_START ... BED_MESH=adaptive` / `PRINT_END` and inherits the system
Neptune 4 Pro) and two stub presets that only inherit the system process and filament by
name (`0.20mm Standard @Elegoo N4Pro 0.4 nozzle`, `Generic PLA @Elegoo`). The user
process/filament files are not used: they carry values a new version can reject (2.4.2
refused `small_perimeter_speed: "0"`). Process = layer height (material-agnostic); the
filament picks the temps (verified on 2.5.0: PETG 240, PLA 205).

OrcaSlicer 2.4 renamed the Elegoo system presets (`Elegoo Neptune 4 Pro (0.4
nozzle)` -> `Elegoo Neptune 4 Pro 0.4 nozzle`, `@Elegoo Neptune4Pro (0.4 nozzle)`
-> `@Elegoo N4Pro 0.4 nozzle`); the names in `server.py` follow the image tag, and
`apps/orcaslicer/check-presets.sh <tag>` proves the user presets still resolve.

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

## Clients (the front-door surfaces)

Reachable at `https://print.kblab.me` (LAN/Tailscale-gated ingress). Both are thin
clients of the API above; set `PRINT_SERVICE_URL` to override (e.g. a port-forward).

- **`client/print`** - CLI, the shell/skill surface an agent shells out to:
  `print printers` | `print slice <f.stl> [--filament pla|petg] [--process standard|fine|draft]`
  | `print send <f.stl> [--no-start]` | `print status` | `print cancel`. Add `--json` anywhere.
- **`mcp/print_mcp.py`** - MCP server (tools: `list_printers`, `slice_model`,
  `print_model`, `printer_status`, `cancel_print`), the scoped structured surface for
  Claude Code / the voice orchestrator. Self-contained `uv run` (PEP 723
  inline deps, no persistent install). Registered in the repo `.mcp.json` as `print`.

## Deploy

GitOps only: edit -> commit -> push to **forgejo** -> Flux reconciles (never
`kubectl apply`). Registered in `apps/kustomization.yaml`. The `client/` and `mcp/`
dirs are plain repo files (not referenced by the kustomization, so Flux ignores them).

## Follow-ups

- **Live Moonraker dispatch test** once the Neptune is powered on (upload +
  print/start + cancel).
- **Bambu A1** dispatch via ha-bambulab (FTP + MQTT print-project).
- **Upload-from-request** (accept an STL in the POST body instead of a NAS
  filename) and a small durable job/queue once the orchestrator drives it.
- **More materials / nozzles** as needed (extend the `processes`/`filaments`
  maps).
