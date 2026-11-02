# wall-ambient

The weekday (Mon-Sat, 07:00-22:00) screensaver page for the wall panels. Schedule and layout: `docs/wall-panels.md` -> Screensaver schedule.

- `index.html` + `ambient.js`, served by unprivileged nginx from a ConfigMap. No image build: edit the files, merge, and Flux rolls the pod.
- The page shows the "Wall Scenery" album through immich-kiosk (`?user=wall&album=<id>`, which keeps its cross-fade, clock, weather and image credits) and, after every few slides, fades to a full-screen WebGL shader scene with its own clock.
- `https://wall-ambient.kblab.me`, LAN/Tailscale only (`monitoring-local-network-only`).

## Query params

| Param | Default | Meaning |
|-------|---------|---------|
| `scenery` | 4 | Scenery slides between shader scenes |
| `slide_secs` | 30 | Seconds per scenery slide |
| `trippy_secs` | 45 | Seconds per shader scene; 0 turns them off |
| `shaders` | all | Comma list: plasma, flow, kaleido, julia, tunnel, aurora |
| `render_scale` | 0.5 | Shader resolution relative to the screen; lower it if a tablet stutters |
| `album`, `user`, `kiosk` | Wall Scenery, wall, immich-kiosk.kblab.me | Which slideshow to show |

## Preview
`https://wall-ambient.kblab.me/?scenery=0.05&shaders=julia` jumps straight to one shader scene.
