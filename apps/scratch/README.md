# scratch — public scratchpad (MicroBin)

A public, password-gated pastebin at **https://scratch.kennethblack.me** for
quick cross-machine notes and ferrying snippets/keys between boxes. Runs
[MicroBin](https://microbin.eu) `2.1.0`.

## Access

HTTP basic auth gates the **entire** instance — there is no anonymous read.
It's a deliberately dead-simple shared login you can hand to anyone:

- **Username:** `scratch`
- **Password:** `scratch`

Same word for both — nothing to remember, nothing per-user. The password is
stored SOPS-encrypted in `secret.yaml` (`basic-auth-password`).

Browser prompts once and remembers it. For scripts:
`curl -u scratch:scratch https://scratch.kennethblack.me/...`.

A separate `admin` user (its own password in `secret.yaml`, **not** shared) can
manage/delete any paste — keep that one private so shared users can't purge the
store.

Both passwords are injected into the container via `secretKeyRef` (not MicroBin's
`file://` prefix — image `2.1.0` does not resolve `file://` for the basic-auth
password and silently rejects every login if you use it).

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

Public reachability + a single shared password. The real protection is the
retention discipline: **sensitive → Burn/Ephemeral (self-destructs), only
non-sensitive → Keep.** If the password ever leaks, only the "Keep" store is
exposed. TLS is mandatory (cert-manager `letsencrypt-dns`). This is deliberately
a low-stakes scratch box, not a secrets manager.

To rotate the key: edit `secret.yaml` (decrypt with `sops`, change, re-encrypt),
commit, and Flux rolls it. Then `kubectl -n scratch rollout restart deploy/scratch`.

## Public exposure (Cloudflare tunnel)

Unlike LAN services, this drops the `monitoring-local-network-only` middleware.
Reachability is the standard flow — see **[`docs/public-sites.md`](../../docs/public-sites.md)**
(canonical reference): this Ingress is all that's needed. external-dns creates the proxied
CNAME and the tunnel's single wildcard catch-all forwards every host to Traefik. No per-host
tunnel route, no DNS record, no Cloudflare token.

## Storage

2Gi `local-path` PVC — node-local, NOT replicated and NOT backed up. Acceptable
because the data is throwaway by design (most of it self-destructs).
