# ntfy

Self-hosted push notification broker at `ntfy.kblab.me`. Used as the
phone-friendly leg of the `~/.notes` multi-device sync fan-out.

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

## Topic

`notes-sync`. Payload is the git ref (`refs/heads/master`) — subscribers
ignore the body and just run `notes-sync` when any message arrives.

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
