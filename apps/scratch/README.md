# scratch — LAN scratchpad (MicroBin)

An **anonymous, LAN-only** pastebin at **https://scratch.kblab.me** for quick
cross-machine notes and ferrying snippets/keys between boxes. Runs
[MicroBin](https://microbin.eu) `2.1.0`.

## Access

**No login.** MicroBin's basic-auth was removed on purpose — image `2.1.0`'s
login is broken: a wrong password can't be retried (the session cookie sticks
and the app never re-prompts), which left the box un-loginnable. Rather than
fight it, auth is gone entirely and protection moved to the network layer.

Reachability is **LAN / Tailscale only**:

- The host lives on the private **`kblab.me`** zone (not the public
  `kennethblack.me` one), so it isn't published to the open internet.
- The `monitoring-local-network-only@kubernetescrd` middleware on the Ingress
  restricts source ranges to RFC1918 + loopback (see
  `apps/monitoring/middleware-ip-allowlist.yaml`).

Open it in a browser on the LAN, or from scripts:
`curl https://scratch.kblab.me/...` — no credentials needed.

> **Want it public again?** Put the host back on the `kennethblack.me` zone,
> drop the `local-network-only` middleware in `ingress.yaml`, and set
> `MICROBIN_PUBLIC_PATH` back. But note MicroBin's own auth is broken, so a
> public instance would be **fully anonymous to the internet** — front it with
> an auth proxy (e.g. traefik forward-auth) rather than MicroBin's basic-auth.

## Retention model — pick a mode per paste

The box is **sensitive-by-default**: a new paste expires in **1 hour** unless
you change the dropdown.

| Mode | How | Use for |
|------|-----|---------|
| 🔥 **Burn** | tick *burn after reading* | One-shot secret transfer — destroyed the instant it's opened on the other machine. Best for SSH/API keys. |
| ⏱️ **Ephemeral** | expiry `1 hour` (**default**) | Sensitive but you may open it a few times — gone within the hour regardless. |
| 📌 **Keep** | expiry `never` | Non-sensitive notes you want to stick around. |

`MICROBIN_DEFAULT_EXPIRY=1hour`, `MICROBIN_MAX_EXPIRY=never`,
`MICROBIN_ENABLE_BURN_AFTER=true` enforce this.

## Security posture

LAN-only reachability + no login. Since there's no auth, the retention
discipline is the real safety net: **sensitive → Burn/Ephemeral (self-destructs),
only non-sensitive → Keep.** TLS is still mandatory (cert-manager
`letsencrypt-dns`). This is deliberately a low-stakes scratch box, not a secrets
manager.

## Storage

2Gi `local-path` PVC — node-local, NOT replicated and NOT backed up. Acceptable
because the data is throwaway by design (most of it self-destructs).
