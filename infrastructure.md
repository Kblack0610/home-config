# infrastructure status

> **last updated:** 2026-03-19
> **updated by:** kblack0610
> **repo:** home-config

## quick status

| environment | provider | nodes | apps | health | issues |
|-------------|----------|------:|-----:|--------|-------:|
| home-k3s | Raspberry Pi + CachyOS (local) | 8 | 11 | degraded | 2 |
| do-nyc3-prod | DigitalOcean (managed) | 2 | 4 | healthy | 0 |
| mac-machines | Apple Silicon (local) | 2 | 3 | healthy | 0 |
| standalone | Docker Compose / embedded | 3 | 3 | healthy | 0 |

## known issues

| cluster | namespace | resource | issue | age | priority |
|---------|-----------|----------|-------|----:|----------|
| home-k3s | pick-a-number | pick-a-number-api | ImagePullBackOff | 69d | low |
| home-k3s | portfolio | portfolio-web | ErrImageNeverPull | 66d | low |
| home-k3s | monitoring | prometheus-node-exporter (x1) | Pending | 53d | low |

> Issues 1-2 are abandoned projects with stale/missing images. Consider deleting the namespaces.
> Issue 3 is a DaemonSet pod that cannot schedule on all nodes (expected on resource-constrained Pi cluster).

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
| registry | Docker container registry | active |
| pick-a-number | Abandoned game app | stale |
| portfolio | Abandoned portfolio site | stale |

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

### do-nyc3-placemyparents-k8s-prod (digitalocean)

| property | value |
|----------|-------|
| api server | `https://828479fe-...k8s.ondigitalocean.com` |
| distribution | DOKS (managed) |
| nodes | 2 (worker-pool-pd3fd, worker-pool-pd3fv) |
| cni | Cilium (with Hubble) |
| csi | DigitalOcean CSI |
| ingress | NGINX Ingress Controller v1.8.1 |
| monitoring | kube-prometheus-stack |

#### namespaces

| namespace | purpose | status |
|-----------|---------|--------|
| ingress-nginx | NGINX Ingress Controller | active |
| monitoring | Prometheus / Grafana / AlertManager | active |
| placemyparents | PlaceMyParents (API + Web) | active |

#### helm releases

| release | namespace | chart version |
|---------|-----------|---------------|
| kube-prometheus-stack | monitoring | v0.87.1 |

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
| PlaceMyParents API | do-nyc3-prod | placemyparents | Deployment | active |
| PlaceMyParents Web | do-nyc3-prod | placemyparents | Deployment | active |
| Prometheus | do-nyc3-prod | monitoring | StatefulSet | active |
| Grafana | do-nyc3-prod | monitoring | Deployment | active |
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

### ingress split

The repository uses two ingress controllers in two different environments:

- `home-k3s`: Traefik on the local network path behind `192.168.1.124`
- `do-nyc3-prod`: nginx ingress inside the DigitalOcean cluster

`*.kblab.me` local DNS rewrites should continue to target the Traefik endpoint on `192.168.1.124`
unless a specific hostname is intentionally migrated.

### public domains

| domain | service | cluster | tls |
|--------|---------|---------|-----|
| finance.kblab.me | Actual Budget | home-k3s | Let's Encrypt |
| git.kblab.me | Forgejo | home-k3s | Let's Encrypt |

> Public ingresses are protected by the CrowdSec bouncer Traefik middleware (`crowdsec-bouncer@kubernetescrd`).

### internal / LAN

| hostname | service | cluster | notes |
|----------|---------|---------|-------|
| hass.kblab.me | Home Assistant | home-k3s | Traefik Ingress |
| grafana.kblab.me | Grafana | home-k3s | Traefik Ingress |
| prometheus.kblab.me | Prometheus | home-k3s | Traefik Ingress |

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
| Prometheus | do-nyc3-prod | Metrics collection |
| Grafana | do-nyc3-prod | Dashboards |
| AlertManager | do-nyc3-prod | Alert routing |

### mcp integrations

| server | purpose |
|--------|---------|
| kubernetes | Cluster management via AI agent |
| home-assistant | Smart home control via AI agent |
| ssh | Node shell access via AI agent |
| prometheus | Metric queries via AI agent |
| grafana | Dashboard management via AI agent |
| digitalocean | Cloud resource management via AI agent |

---

## networking

| cluster | cni | ingress | dns |
|---------|-----|---------|-----|
| home-k3s | Flannel | Traefik | AdGuard Home (Pi3 standalone) |
| do-nyc3-prod | Cilium | NGINX | DigitalOcean managed |

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
