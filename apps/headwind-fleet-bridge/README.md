# headwind-fleet-bridge

Mirrors Headwind (Android) device liveness into the gatus `fleet` group, so tablets
appear in the same fleet-pulse health view as the computers.

Android devices managed by Headwind don't push fleet-pulse heartbeats themselves.
This long-running bridge (in the `headwind-mdm` namespace) reads the hmdm `devices`
table every 60s and POSTs a heartbeat per device to gatus:

```
POST http://gatus.gatus.svc:8080/api/v1/endpoints/fleet_<device>/external?success=<seen-recently>
```

- **Liveness source:** Headwind's `lastupdate` (epoch ms of the device's last sync). A
  device is "up" if Headwind saw it within `DEVICE_STALE_AFTER` (default 900s / 15 min).
- **Why 60s loop:** the bridge pushes more often than the readers' 180s staleness
  window, so the gatus result *timestamp* always stays fresh; the success *bool* carries
  the real device liveness. (Android syncs infrequently, so we can't rely on the device's
  own timestamp being < 180s old - the bridge's push is the fresh signal.)
- **Read-only:** only SELECTs `devices`. Reuses `headwind-mdm-secret/sql-pass`.
- **Fail-open:** a gatus 404 (device not declared as an external-endpoint yet) is logged
  and skipped; `lastupdate = 0` (never-synced / HMDM's template device) is skipped.

## Adding a device to the fleet view

When you enroll a tablet in Headwind, do two things so it shows up:

1. **Declare it in gatus** - add an `external-endpoints` entry to `apps/gatus/configmap.yaml`
   (`group: fleet`, `name: <device>`), matching the device's Headwind `number` after
   gatus's slug rule (lowercase; `/ _ , . space` -> `-`). The bridge logs the exact key it
   tries (`fleet_<slug>`); use that.
2. **Add it to the readers' roster** - append the same `fleet_<slug>` name to `FLEET_ROSTER`
   in `~/.config/fleet-pulse/env` so the bar counts it (see the fleet-pulse README: absent
   from the roster = uncounted; absent from the API but in the roster = amber).

Until step 1, the bridge just logs `skip fleet_<slug>: not declared in gatus` - harmless.

## Verify

```bash
kubectl --context home-k3s -n headwind-mdm logs deploy/headwind-fleet-bridge --tail=20
# with no enrolled devices: "no synced devices yet" each loop.
# after enrolling one + declaring it in gatus: "pushed fleet_<device> success=true (HTTP 200)"
```

## Config (deployment env)

| Var | Default | Meaning |
|-----|---------|---------|
| `INTERVAL` | `60` | seconds between polls |
| `DEVICE_STALE_AFTER` | `900` | device counted "up" if seen by Headwind within this many seconds |
| `GATUS_URL` | `http://gatus.gatus.svc.cluster.local:8080` | in-cluster gatus |
| `DRY_RUN` | `false` | log intended pushes without sending |
