# Public sites — the one and only way to expose something

> **Canonical reference.** If another doc, skill, or runbook tells you to edit the
> Cloudflare tunnel config, add a per-host tunnel route, create a DNS record by hand,
> or use `CLOUDFLARE_TUNNEL_API_TOKEN` / a `cfd_tunnel/.../configurations` PUT to put a
> site online — **it is wrong and out of date.** This file supersedes it.

## How to add a public site

1. Add a Kubernetes **Ingress** under `apps/<your-app>/` (host `<name>.<zone>`,
   `ingressClassName: traefik`, TLS via `cert-manager.io/cluster-issuer: letsencrypt-dns`).
2. `git push` to forgejo.

That's it. There is no step 3. No tunnel edit, no DNS record to create, no Cloudflare
token anywhere in this flow.

Mirror an existing one: `apps/scratch/` (simple), `apps/mem0/ingress-public.yaml`
(with middlewares), `apps/forgejo/ingress-public.yaml` (with crowdsec).

## Why that's all it takes — the architecture

```
your Ingress (Git/Flux)
   │
   ├─▶ external-dns reads it → creates the proxied CNAME  <name>.<zone> → tunnel
   │      (apps/external-dns/, in-cluster DNS:Edit token — never hand-pasted)
   │
   └─▶ Cloudflare tunnel `public-sites-homelab` has ONE rule: wildcard catch-all
          → https://traefik.kube-system.svc.cluster.local:443
                │
                └─▶ Traefik host-matches your Ingress → your Service
                       (TLS via cert-manager letsencrypt-dns)
```

Two systems, each with **one** job and **one** source of truth:

| Concern | Source of truth | Mechanism |
|---|---|---|
| **Routing** (which host → which service) | the Kubernetes **Ingress** (Git/Flux) | tunnel forwards *everything* to Traefik; Traefik routes by Host header |
| **DNS** (the proxied CNAME → tunnel) | the same **Ingress** | `external-dns` derives it automatically, all public zones |
| **TLS** | cert-manager `letsencrypt-dns` | per-host cert from the Ingress |
| **The tunnel itself** | Terraform `bnb/platform/infra/public-sites-tunnel` | the connector + the single wildcard rule; rarely touched |

The tunnel holds **exactly one** ingress rule (the wildcard). Per-host tunnel routes do
not exist anymore — that was the legacy method that required a rotating
`Cloudflare-Tunnel:Edit` token and made every new site depend on it.

## What is deliberately NOT in the add-a-site path

- **No `CLOUDFLARE_TUNNEL_API_TOKEN`.** The in-cluster token external-dns uses is
  **DNS:Edit only** (locked down — see below). It cannot edit the tunnel.
- **No Terraform run.** Terraform manages the tunnel *shape*, not your site. DNS is **not**
  in Terraform at all — external-dns owns it.
- **No manual Cloudflare dashboard step.**

## The tunnel-edit capability is locked down on purpose

The cluster token has had `Account:Cloudflare-Tunnel:Edit` **removed**. A
`cfd_tunnel/.../configurations` PUT returns `Authentication error`. This is intentional:
it makes the legacy per-host method impossible, so the Ingress flow above is the *only*
path. To make a rare change to the tunnel *shape* (e.g. the wildcard target), temporarily
re-grant `Cloudflare-Tunnel:Edit` to the "Edit zone DNS" token in the Cloudflare dashboard,
make the change, then remove it again.

## Zones

`external-dns` (`apps/external-dns/`) owns DNS for **kennethblack.me**,
**blacknbrownstudios.com**, **binks.chat**, **kblack.dev** (`--domain-filter`, policy
`upsert-only`). It reads standard Ingresses **and** Traefik `IngressRoute`s
(`--source=traefik-proxy`, for binks.chat). `*.kblab.me` is **LAN-only** via AdGuard — never
the tunnel — and is out of scope here.

## Troubleshooting (not part of adding a site)

- **A new host 404s from the internet** but works in-cluster
  (`curl -k -H 'Host: <h>' https://traefik.kube-system.svc.cluster.local:443/`): the Ingress
  is fine; check that external-dns created the CNAME (`dig +short <h>` → Cloudflare IPs) and
  its logs (`kubectl -n external-dns logs deploy/external-dns`).
- **522 / connection errors:** tunnel connector down — `kubectl -n apps logs -l
  app=cloudflared-public-sites`.
- The Cloudflare REST API (`cfd_tunnel`, `dns_records`) is for **diagnosis only** now, not
  for putting sites online.
