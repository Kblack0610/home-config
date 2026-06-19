# scratch — public scratchpad (MicroBin)

A public, password-gated pastebin at **https://scratch.kennethblack.me** for
quick cross-machine notes and ferrying snippets/keys between boxes. Runs
[MicroBin](https://microbin.eu) `2.1.0`.

## Access

HTTP basic auth gates the **entire** instance — there is no anonymous read.

- **Username:** `scratch`
- **Password:** the shared key (stored SOPS-encrypted in `secret.yaml`)

Browser prompts for it. For scripts: `curl -u scratch:<key> https://scratch.kennethblack.me/...`.

An `admin` user (separate password, also in `secret.yaml`) can manage/delete
any paste.

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
Reachability from the internet is via the `cloudflared-public-sites` tunnel
(`apps/cloudflared-public-sites/`). The public-hostname route
`scratch.kennethblack.me → https://traefik` is configured on the tunnel
(same target as the other public sites). The tunnel is token/remote-managed, so
that mapping lives in Cloudflare, not in this repo.

## Storage

2Gi `local-path` PVC — node-local, NOT replicated and NOT backed up. Acceptable
because the data is throwaway by design (most of it self-destructs).
