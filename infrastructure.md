# infrastructure status

> **last updated:** 2026-03-30
> **updated by:** kblack0610
> **repo:** home-config

## quick status

| environment | provider | nodes | apps | health | issues |
|-------------|----------|------:|-----:|--------|-------:|
| home-k3s | Raspberry Pi + CachyOS (local) | 9 | 16 | degraded | 1 |
| mac-machines | Apple Silicon (local) | 2 | 3 | healthy | 0 |
| standalone | Docker Compose / embedded | 3 | 3 | healthy | 0 |

## known issues

| cluster | namespace | resource | issue | age | priority |
|---------|-----------|----------|-------|----:|----------|
| home-k3s | monitoring | prometheus-node-exporter (x1) | Pending | 53d | low |

> Issue is a DaemonSet pod that cannot schedule on all nodes (expected on resource-constrained Pi cluster).

---

## clusters

### home-k3s (local raspberry pi)

| property | value |
|----------|-------|
| api server | `https://192.168.1.20:6443` |
| distribution | K3s |
| nodes | 6x Raspberry Pi |
| cni | Flannel (K3s default) |
| ingress | Traefik v3.3.2 |
| monitoring | kube-prometheus-stack (Prometheus + Grafana + node_exporter) |
| avg cpu | 1-3% per node |
| avg memory | 18-42% per node |

#### nodes

| node | cpu | memory |
|------|-----|--------|
| raspberrypi | 135m (3%) | 3429Mi (42%) |
| raspberrypi-23a7710c | 44m (1%) | 983Mi (24%) |
| raspberrypi-771be84c | 97m (2%) | 1461Mi (18%) |
| raspberrypi-b814834e | 125m (3%) | 782Mi (42%) |
| raspberrypi-e3a771f1 | 58m (1%) | 1158Mi (28%) |
| raspberrypi-7386c525 | -- | -- |

#### namespaces

| namespace | purpose | status |
|-----------|---------|--------|
| actual-budget | Personal finance (Actual Budget) | active |
| apps | PlaceMyParents (API + Web + DB) | active |
| forgejo | Git hosting (Forgejo) | active |
| home-assistant | Smart home hub | active |
| history-time | History-Time app (PostgreSQL) | active |
| headscale | Tailscale VPN (self-hosted) | suspended |
| monitoring | Prometheus / Grafana / AlertManager | active |
| crowdsec | CrowdSec IPS (LAPI + bouncer) | active |
| openclaw | OpenClaw gateway and Control UI | active |
| orcaslicer | 3D printer slicer (OrcaSlicer) | active |
| portfolio | Kenneth Black portfolio (kennethblack.me) | active |
| bnb-studios | BNB Studios site (blacknbrownstudios.com) | active |
| black-dev | kblack.dev personal site | active |
| registry | Docker container registry | active |

#### helm releases

| release | namespace | chart version |
|---------|-----------|---------------|
| kube-prometheus-stack | monitoring | v0.87.1 |
| placemyparents-api | apps | v0.1.0 |
| placemyparents-web | apps | v1.0.0 |
| crowdsec | crowdsec | crowdsec/crowdsec |
| traefik | kube-system | v3.3.2 |
| traefik-crd | kube-system | v3.3.2 |

---

## applications

### service directory

| app | cluster | namespace | type | status |
|-----|---------|-----------|------|--------|
| Home Assistant | home-k3s | home-assistant | Deployment | active |
| PlaceMyParents API | home-k3s | apps | Helm | active |
| PlaceMyParents Web | home-k3s | apps | Helm | active |
| PlaceMyParents DB | home-k3s | apps | StatefulSet | active |
| Actual Budget | home-k3s | actual-budget | Deployment | active |
| History-Time DB | home-k3s | history-time | StatefulSet | active |
| Docker Registry | home-k3s | registry | Deployment | active |
| Forgejo | home-k3s | forgejo | Deployment | active |
| Prometheus | home-k3s | monitoring | StatefulSet | active |
| Grafana | home-k3s | monitoring | Deployment | active |
| CrowdSec | home-k3s | crowdsec | HelmRelease | active |
| OpenClaw | home-k3s | openclaw | Deployment | active |
| OrcaSlicer | home-k3s | orcaslicer | Deployment | active |
| Kenneth Black Portfolio | home-k3s | portfolio | Deployment | active |
| BNB Studios | home-k3s | bnb-studios | Deployment | active |
| kblack.dev | home-k3s | black-dev | Deployment | active |
| Frigate NVR | standalone | -- | Docker Compose | active |
| Pi3 AdGuard Home | standalone | -- | Docker Compose | active |
| ESP32 Smart Switch | standalone | -- | Embedded (Rust) | active |
| GitHub Actions Runner | mac-machines | -- | LaunchDaemon | active |
| MLX LLM Inference | mac-machines | -- | Native (launchd) | active |
| node_exporter | mac-machines | -- | Homebrew service | active |

---

### mac-machines (apple silicon)

| property | value |
|----------|-------|
| machines | Mac Studio M3 Ultra (512GB), Mac Mini M1 (16GB) |
| purpose | iOS builds, Expo, GitHub Actions CI/CD, LLM inference |
| monitoring | node_exporter (port 9100) → Prometheus (ServiceMonitor) → Grafana / HA |
| not in K3s | Native macOS workloads only (K3s is Linux-only) |

#### nodes

| machine | chip | ram | ip | hostname | role |
|---------|------|-----|-----|----------|------|
| Mac Studio | M3 Ultra | 512 GB | 192.168.1.4 | mac-studio | MLX inference, Ollama fallback, iOS builds, GH Actions runner |
| Mac Mini | M1 | 16 GB | 192.168.1.7 | pc-home-m1-mini | iOS builds, Expo, GH Actions runner |

#### setup docs

See [Mac Machines Setup Guide](docs/mac-machines.md) for full provisioning instructions.

---

## external access

### ingress

Traefik on the local network path behind `192.168.1.124`. All `*.kblab.me` local DNS rewrites target this endpoint via AdGuard Home wildcard rewrite.

### public domains

| domain | service | cluster | tls | routing |
|--------|---------|---------|-----|---------|
| kennethblack.me | Portfolio | home-k3s | Let's Encrypt | Cloudflare tunnel → Traefik |
| blacknbrownstudios.com | BNB Studios | home-k3s | Let's Encrypt | Cloudflare tunnel → Traefik |
| kblack.dev | kblack.dev | home-k3s | Let's Encrypt | Cloudflare tunnel → Traefik |
| finance.kblab.me | Actual Budget | home-k3s | Let's Encrypt | Direct Traefik |
| git.kblab.me | Forgejo | home-k3s | Let's Encrypt | Direct Traefik |

> Public ingresses are protected by the CrowdSec bouncer Traefik middleware (`crowdsec-bouncer@kubernetescrd`).
> Public sites (kennethblack.me, blacknbrownstudios.com, kblack.dev) route through a Cloudflare tunnel (`cloudflared-public-sites` in the `apps` namespace).

### internal / LAN

| hostname | service | cluster | notes |
|----------|---------|---------|-------|
| hass.kblab.me | Home Assistant | home-k3s | Traefik Ingress |
| grafana.kblab.me | Grafana | home-k3s | Traefik Ingress |
| openclaw.kblab.me | OpenClaw | home-k3s | Traefik Ingress |
| prometheus.kblab.me | Prometheus | home-k3s | Traefik Ingress |
| slicer.kblab.me | OrcaSlicer | home-k3s | Traefik Ingress |

---

## backups

| service | schedule | retention | host path |
|---------|----------|-----------|-----------|
| Actual Budget | daily 3:00 AM | 30 backups | `/var/backups/actual-budget/` |
| Forgejo | daily 3:00 AM | 30 backups | `/var/backups/forgejo/` |

### manual backup commands

```bash
# trigger actual budget backup
kubectl create job --from=cronjob/actual-budget-backup manual-backup-$(date +%s) -n actual-budget

# trigger forgejo backup
kubectl create job --from=cronjob/forgejo-backup manual-backup-$(date +%s) -n forgejo
```

---

## monitoring

| tool | cluster | purpose |
|------|---------|---------|
| Prometheus | home-k3s | Metrics collection |
| Grafana | home-k3s | Dashboards |
| AlertManager | home-k3s | Alert routing |
| CrowdSec | home-k3s | Intrusion prevention (LAPI + bouncer) |
| Gatus | home-k3s | Uptime / health checks |
| node_exporter | mac-machines | CPU/RAM/Disk metrics (Homebrew service) |

### mcp integrations

| server | purpose |
|--------|---------|
| kubernetes | Cluster management via AI agent |
| home-assistant | Smart home control via AI agent |
| ssh | Node shell access via AI agent |
| prometheus | Metric queries via AI agent |
| grafana | Dashboard management via AI agent |

---

## networking

| cluster | cni | ingress | dns |
|---------|-----|---------|-----|
| home-k3s | Flannel | Traefik | AdGuard Home (Pi3 standalone) |

---

## repo separation: home-config vs bnb/platform

All K8s manifests deployed to the **home-k3s cluster** live in this repo (`home-config`), managed by Flux with `prune: true`. This includes public-facing sites like `kennethblack.me` even though their application source code lives in `bnb/platform`.

| concern | repo | why |
|---------|------|-----|
| K8s manifests (all cluster services) | `home-config` | Single Flux source of truth, self-healing via `prune: true` |
| Cloudflare tunnel Terraform | `bnb/platform` | Tunnel provisioning is infrastructure owned by the platform team |
| Application source code | `bnb/platform` | App code, Dockerfiles, build pipelines |
| Container images | Local registry (`192.168.1.20:30500`) | Built from `bnb/platform`, pushed to local registry, consumed by home-config manifests |
| DigitalOcean deployments | `bnb/platform` | DO App Platform specs for production apps |

**Key rule:** If it runs on home-k3s, the manifest lives in `home-config`. If it runs on DigitalOcean, the manifest lives in `bnb/platform`. Cloudflare tunnel config (Terraform) stays in `bnb/platform` since it bridges external DNS to the cluster.

---

## documentation

| document | path |
|----------|------|
| Project overview | `README.md` |
| Infrastructure status | `infrastructure.md` |
| Mac machines guide | `docs/mac-machines.md` |
| Finance stack | `docs/finance-stack.md` |
| MCP servers guide | `docs/MCP-SERVERS.md` |

---

## how to update

Update this document when:
- An app is deployed or removed
- A cluster or node is added/removed
- Known issues are resolved or new ones found
- Backup schedules or domains change

### checklist

1. Update the **last updated** date at the top
2. Update **quick status** table (node counts, app counts, health)
3. Add/remove entries in **known issues**
4. Add/remove entries in the **service directory**
5. Update **external access** if domains changed
6. Update **backups** if schedules changed
7. Commit with message: `docs: update infrastructure status`
