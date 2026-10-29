# ntfy

Self-hosted push notification broker at `ntfy.kblab.me`. It carries the phone pages for agents, infra and the house, and the phone-friendly leg of the `~/.notes` multi-device sync fan-out (below).

The flow:

```
Forgejo /kblack0610/.notes push
        ↓ webhook
notes-sync-bridge  (verifies HMAC)
        ├──→ mosquitto  → desktops/laptops (notes-mqtt subscriber)
        └──→ ntfy       → Android phone (ntfy app, OS push channel)
```

The phone uses the official ntfy Android app (FCM-backed, near-zero battery
cost) subscribed to `https://ntfy.kblab.me/notes-sync`.

## Why no auth

`auth-default-access` is `read-write` because the ingress sits behind the
`monitoring-local-network-only` middleware. Public reachability is gated at
the network layer (Tailscale always-on on the phone routes via LAN). The
bridge publishes via the in-cluster Service (`ntfy.ntfy.svc.cluster.local`),
which never traverses the ingress.

If we ever need to expose ntfy publicly, switch `auth-default-access` to
`deny-all`, drop the LAN middleware, and seed an `ntfy user add` flow via an
init container reading from a SOPS-encrypted `secret.yaml`.

## Topics and who subscribes

| Topic | Publisher | Who reads it | Phone |
|---|---|---|---|
| `agents` | `agent-notify` on the workstation (Sentinel watches, the Briefing, wave rolls, asks) | Ken | yes, Minimum priority High |
| `homelab-alerts` | Alertmanager (`apps/monitoring/helm-values.yaml`) and Home Assistant `fleet_alerts.yaml` | Ken | yes, Minimum priority High |
| `home` | Home Assistant `packages/home_alerts.yaml` (prints, vacuum, litter box, 08:00 digest) | anyone who lives here | optional, Minimum priority Default |
| `notes-sync` | `notes-sync-bridge` on every `.notes` push | the `notes-ntfy` user service and the phone's sync | no buzz wanted, it is machine traffic |
| `homelab-janitor` | `apps/cluster-janitor/cronjob.yaml` | Ken, when debugging the janitor | no |
| `finance` | `apps/actual-budget/bank-sync-cronjob.yaml` | Ken | optional |

### Priorities

ntfy priorities run 1 to 5. Every publisher here uses the same meaning, so one phone setting works for every topic:

| ntfy | Name | Phone with Minimum priority High | Used for |
|---|---|---|---|
| 5 | max / urgent | red, pop-over, long buzz | something is down that you must fix: a node, prod, an overnight test run, a critical Alertmanager alert |
| 4 | high | buzz | needs you today: the Briefing with items for you, spend over budget, a print that failed |
| 3 | default | silent, listed in the app | progress and warnings: a wave rolled, a warning alert, a print finished |
| 1-2 | min / low | silent, listed in the app | self-healing noise and digests |

The `agents` levels are defined in `~/.dotfiles-private/.config/agentctl/SENTINEL.md` (section Paging). Alertmanager sends `severity: critical` at 5 and everything else at 3; `InfoInhibitor` and `Watchdog` are never sent.

### Phone setup (Android)

1. Install the ntfy app (F-Droid or Play Store).
2. Settings -> General -> Default server: `https://ntfy.kblab.me`.
3. Add a subscription for `agents`, then one for `homelab-alerts`. Open each one -> the three-dot menu -> Notification settings -> Minimum priority -> High.
4. Optional: subscribe to `home` with Minimum priority Default if you want print and vacuum pings, or leave it to the wall panels.
5. The server is LAN-only (see Why no auth), so keep Tailscale on to get pages away from home.

Test it from a shell on the LAN: `curl -H 'Priority: urgent' -d 'test' https://ntfy.kblab.me/agents` must arrive red; the same with `Priority: default` must arrive silently.

### Wall panels

The four FreeKiosk wall panels run Home Assistant in lock-task mode, so no ntfy app can draw over them. House events reach them through Home Assistant instead: `script.home_alert` in `apps/home-assistant/config/packages/home_alerts.yaml` sets `input_text.home_alert`, which shows as a banner (`dashboards/_home_alert_card.yaml`) on the wall-panel screensaver and on top of the Home view until someone taps it. The same script posts the event to topic `home` for phones. To add a house event, add an automation there that calls `script.home_alert`.

### notes-sync payload

The payload is the git ref (`refs/heads/master`). Subscribers ignore the body and run `notes-sync` when any message arrives.

## Verification

```bash
# Health
kubectl --context home-k3s -n ntfy get pods
curl -s https://ntfy.kblab.me/v1/health | jq

# Publish from inside the cluster
kubectl --context home-k3s -n ntfy run curl --rm -it --image curlimages/curl -- \
  curl -X POST -d "refs/heads/master" http://ntfy.ntfy.svc.cluster.local/notes-sync

# Subscribe from a desktop (over Tailscale-routed ingress)
curl -sN https://ntfy.kblab.me/notes-sync/json
```

## Backups

Skipped intentionally. The PVC holds a 12h message cache and a (currently
unused) auth file. Loss = devices reconnect, miss any message older than the
last 12h. The 5-minute fallback `git-sync-notes.timer` on every device covers
that gap.
