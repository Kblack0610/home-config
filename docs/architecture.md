# Architecture Walkthrough

Cross-layer reference for how requests, installs, and failures flow through PXE / Ansible / Flux. Read `docs/gitops.md` first for the layer split; this doc is the "stitch it together" companion.

Contents:

- [Three layers at a glance](#three-layers-at-a-glance)
- [Worked example 1: a public request reaches a Flux app](#worked-example-1-a-public-request-reaches-a-flux-app)
- [Worked example 2: a DHCP lease lands](#worked-example-2-a-dhcp-lease-lands)
- [Worked example 3: a new bare-metal node joins](#worked-example-3-a-new-bare-metal-node-joins)
- [GPU wiring: NVIDIA vs AMD](#gpu-wiring-nvidia-vs-amd)
- [Consumer / depends-on map](#consumer--depends-on-map)
- [Failure domains — what breaks if X dies](#failure-domains--what-breaks-if-x-dies)
- [Cluster DR — rebuild from nothing](#cluster-dr--rebuild-from-nothing)
- [Operator gotchas](#operator-gotchas)

## Three layers at a glance

```
                 Git (home-config)
                 ─────────────────
                       │
           ┌───────────┼───────────┐
           │           │           │
          PXE       Ansible       Flux
     (once per    (on-demand,   (continuous
      host, at     idempotent    reconcile,
      cold boot)   re-applies)   in-cluster)
           │           │           │
           ▼           ▼           ▼
       Disk image   systemd/     kube objects
       + SSH key    launchd/      across 36
       + kickstart  brew / UCI    apps/ dirs
```

Each layer has a single source of truth in git:

- PXE → `infrastructure/pxe-server/`
- Ansible → `ansible/`
- Flux → `apps/`, `clusters/home-k3s/flux-system/`

Layer-ownership rules live in `docs/gitops.md`. Phase roadmap for Ansible is in `docs/ansible.md`. Catalog of "what runs where" is `docs/homelab-catalog.md`.

## Worked example 1: a public request reaches a Flux app

Goal: a browser on the public internet hits `https://kennethblack.me`.

```
Browser
  │ HTTPS
  ▼
Cloudflare edge (DNS: kennethblack.me → Cloudflare tunnel CNAME)
  │ cloudflared-public-sites tunnel (TLS terminated at Cloudflare)
  ▼
cloudflared pod (apps/ ns on home-k3s)
  │ plain HTTP to in-cluster Service
  ▼
Traefik ingress (infrastructure/traefik/, kube-system ns)
  │ matches Host header → portfolio Service
  ▼
portfolio Deployment (apps/portfolio/)
```

- DNS is owned by Cloudflare (not this repo). Tunnel provisioning lives in `bnb/platform` per `infrastructure.md:217-229`.
- The tunnel-terminator pod (`cloudflared-public-sites`) lives in `apps/` and is Flux-managed in this repo.
- Traefik is bundled with k3s but pinned in `infrastructure/traefik/`.
- The `portfolio` Deployment ships from `apps/portfolio/`, image built by `bnb/platform` CI and pushed to the local registry `192.168.1.20:30500`.

For LAN traffic (`hass.kblab.me`), the flow skips Cloudflare: client → OpenWRT dnsmasq → AdGuard Home (Pi3) rewrites `*.kblab.me` → `192.168.1.124` → Traefik → Service. See `apps/pi3-adguard-home/README.md` for the DNS-rewrite mechanics.

## Worked example 2: a DHCP lease lands

Goal: `thinkcentre` boots and gets IP `192.168.1.100`.

```
thinkcentre
  │ boot → DHCP DISCOVER (eth0)
  ▼
OpenWRT (192.168.1.1)
  │ dnsmasq reads /etc/config/dhcp
  │ match on MAC → static lease (name=thinkcentre, ip=192.168.1.100)
  ▼
DHCP OFFER → thinkcentre
  │ kernel networking comes up
  ▼
thinkcentre (192.168.1.100) → SSH-reachable from workstation
```

- Source of truth: `infrastructure/dhcp/devices.yaml` (YAML inventory of every MAC/hostname/IP).
- Sync tool: `infrastructure/dhcp/dhcp.sh` (reads `devices.yaml` → `uci set` → `uci commit dhcp` → `/etc/init.d/dnsmasq restart`).
- Phase C draft PR `ansible/roles/openwrt-dhcp/` ports this to Ansible once apply-safety review is done.
- DNS: OpenWRT dnsmasq forwards every query to AdGuard Home at `192.168.1.193` (see `apps/pi3-adguard-home/README.md:69-87`), which has a wildcard rewrite for `*.kblab.me → 192.168.1.124`.

## Worked example 3: a new bare-metal node joins

Goal: a fresh Intel NUC becomes a working k3s agent.

```
1. PXE (day 0)
   - Plug NUC into switch, power on, PXE-boot from pc-home-cachy-main workstation
   - infrastructure/pxe-server/install.sh serves an Arch kickstart
   - kickstart installs CachyOS, enables sshd, seeds kblack0610's authorized_keys
   - (today, from cluster.sh:64-84) inline k3s install with K3S_URL + K3S_TOKEN
   - node reboots, joins cluster as k3s agent

2. Ansible (day 1+)
   - Add a host entry to ansible/inventory.yml under linux_bare_metal
   - Seed ansible/group_vars/linux_bare_metal/vault.yml if any secret is needed
   - Optionally bind roles: github-actions-runner-linux, k3s-agent, etc.
   - Run: ansible-playbook playbooks/site.yml --limit <host> --check --diff
   - Apply, then verify via systemctl / kubectl get nodes

3. Flux (if it's a k3s node)
   - Kubernetes sees the node via kubelet registration
   - Flux scheduler places pods that match the node's labels / capacity
   - New labels (e.g., nvidia.com/gpu.present) light up DaemonSets like nvidia-device-plugin
```

Phase B `k3s-agent` role exists under `ansible/roles/k3s-agent/` but is authored-unbound — the inline PXE install in `cluster.sh` still owns this today. Full migration moves step 1's k3s install into step 2.

## GPU wiring: NVIDIA vs AMD

Two different paths for two different GPU ecosystems:

| Aspect | NVIDIA path | AMD path |
|--------|-------------|----------|
| Nodes | `hp-victus` | `asus-laptop` |
| Apps | `comfyui`, `litellm`, `openclaw` | `immich`, `orcaslicer` |
| Device plugin | `apps/nvidia-device-plugin/daemonset.yaml` (targets `nvidia.com/gpu.present: "true"`) | **None** — no AMD device plugin exists |
| Pod access | Standard k8s resource request: `nvidia.com/gpu: "1"` + `runtimeClassName: nvidia` + `nodeSelector: nvidia.com/gpu.present` | Privileged pod + host `/dev` passthrough. Explicitly documented in `apps/orcaslicer/deployment.yaml` comments: "Privileged containers get all host devices auto-populated into the container's /dev tmpfs" |
| Runtime | `nvidia-container-runtime` (auto-registered `nvidia` RuntimeClass by k3s) | Default containerd runtime + privileged mode |
| Node label | `nvidia.com/gpu.present=true` (set by `infrastructure/pxe-server/.../nvidia-gpu.sh` or manually) | None — pinned by `kubernetes.io/hostname: asus-laptop` |

Consequence: GPU-aware apps on `hp-victus` look like normal k8s GPU workloads. GPU-aware apps on `asus-laptop` look like privileged pods with hostname pinning. An agent debugging "Immich's GPU isn't being used" should NOT grep for device plugins — should check that the pod is running privileged and scheduled on `asus-laptop`.

## Consumer / depends-on map

Partial graph — focuses on the dependencies an agent is most likely to hit during debugging:

```
litellm (in-cluster, hp-victus)
  ├── mac-studio:8080  (MLX code — launchd on mac-studio)
  ├── mac-studio:8081  (MLX smart)
  └── mac-studio:8082  (MLX reasoning)
       (these three are bare-metal services; see ansible/roles/launchd-mlx-services/)

home-assistant → mosquitto (MQTT broker, in-cluster)
                → mqtt2prom → monitoring (Prometheus)

frigate (pi docker-compose) → home-assistant (via MQTT)

apps requiring NAS (backups / media):
  - actual-budget → nas Samba share
  - forgejo → nas (backups)
  - immich → nas (library storage)
  - jellyfin → nas (media)
(nas itself runs as a k3s pod on asus-laptop per apps/nas/)

public apps behind the public Cloudflare tunnel:
  kennethblack.me, blacknbrownstudios.com, kblack.dev, binks.chat
       → cloudflared-public-sites pod (in-cluster)
       → Traefik → target Service

LAN apps behind AdGuard rewrite (*.kblab.me → 192.168.1.124):
  hass, grafana, prometheus, openclaw, slicer, finance, git, neptune, gatus, ...
       → Traefik → target Service

Apex redirect (kblab.me → HA Launcher):
  kblab.me → Traefik → home-assistant-apex Ingress
       → `kblab-apex-redirect` Middleware (RedirectRegex)
       → 307 → https://hass.kblab.me/lovelace/launcher
```

Gatus (`apps/gatus/`) monitors every endpoint in this graph — the Gatus configmap is the concrete enumeration of "what depends on what network-reachability-wise."

## Failure domains — what breaks if X dies

| Component | What goes down immediately | What stays up |
|-----------|----------------------------|---------------|
| `pi5-master` (k3s server) | Every k3s pod on every node — no scheduler, no API | All bare-metal services: MLX, Mac runners, thinkcentre Linux runner. OpenWRT DHCP/DNS. AdGuard Pi3. Frigate. |
| OpenWRT router (192.168.1.1) | DHCP new-client onboarding, DNS resolution via the router's dnsmasq | Static IPs keep routing LAN-to-LAN. External internet is gone (OpenWRT is the gateway). AdGuard Pi3 keeps answering for clients that hit it directly. |
| AdGuard Home (pi3, 192.168.1.193) | `*.kblab.me` resolution from any client | Everything else — OpenWRT falls back through its own dnsmasq |
| mac-studio | MLX inference (all three models) | LiteLLM still serves — but every request that routes to a MLX upstream 500s |
| NAS (asus-laptop pod) | Actual Budget backup jobs, Jellyfin media, Immich library | Home Assistant (no NAS dep), Forgejo (backups queue, app keeps running) |
| cloudflared-public-sites pod | Public HTTPS access to `kennethblack.me`, `blacknbrownstudios.com`, `kblack.dev`, `binks.chat` | LAN access (via AdGuard rewrite path) still works for non-public services |
| Traefik | Every ingress, LAN and public | Everything that doesn't need HTTPS ingress (node_exporter on :9100, mosquitto on :1883) |
| Flux itself | Zero immediate impact on running workloads; new deploys + reconciliation just don't happen until recovered | Everything currently running |

## Cluster DR — rebuild from nothing

Full sequence for "the home-k3s cluster is wiped and needs to come back." Drawn from `docs/gitops.md:78-110` (bootstrap) + `docs/backup-runbook.md` (data restore) + PXE/Ansible.

1. **Workstation prerequisites.** Ensure `flux`, `sops`, `age`, `kubectl`, `ansible` CLIs available and the Age private key (`~/.config/sops/age/keys.txt`) is restored from backup. Without the Age key, committed secrets cannot be decrypted — they are not recoverable from git alone.
2. **Rebuild a control-plane node.** PXE-boot `pi5-master` from the workstation → cluster.sh kickstart installs k3s as server with `K3S_TOKEN` → node reaches Ready.
3. **(Optional) rebuild agent nodes.** Same PXE flow with `K3S_URL` pointing at `pi5-master`.
4. **Flux bootstrap.** From the workstation with `KUBECONFIG` pointing at the new cluster:
   ```
   kubectl create secret generic sops-age \
     -n flux-system --from-file=age.agekey=$HOME/.config/sops/age/keys.txt
   flux bootstrap github --owner=<you> --repository=home-config \
     --branch=master --path=clusters/home-k3s --personal
   ```
5. **Flux reconciles the world.** Every app under `apps/` and every infra manifest under `infrastructure/` shows up over the next few minutes. SOPS-encrypted secrets are decrypted by the sops-age secret from step 4.
6. **Restore per-app data.** Follow `docs/backup-runbook.md` — `actual-budget`, `forgejo`, NAS-backed shares all need their backup restored before the apps come fully up.
7. **(If Mac / runner services were wiped too)** Run Ansible per Phase roadmap — bind `github-actions-runner-linux` / `mac` / `launchd-mlx-services` as needed and `ansible-playbook --limit <host>` against each.
8. **Verify.** `kubectl get pods -A | grep -v Running`, `flux get all -A`, then smoke-test a few ingresses (`curl -I https://hass.kblab.me`, `curl -I https://kennethblack.me`). Gatus dashboard at `status.kblab.me` is the one-glance check once it's up.

The Age key is the single item without which the cluster is unrecoverable. Backup it offline.

## Operator gotchas

- **kubectl context footgun.** Default `kubectl` context is the DigitalOcean cluster, **not** home-k3s. Always run `kubectl config current-context` before any cluster op. Use `kubectl config use-context home-k3s` or set `KUBECONFIG` explicitly for this repo's work. (Captured in agent memory as `feedback_cluster_context.md`; surfaced here because it's a real production-affecting mistake.)
- **Never `kubectl apply` Flux-managed resources.** Flux silently overwrites drift on next reconcile. Path is commit → push → `flux reconcile`. See `docs/gitops.md:112-119`.
- **`*.kblab.me` is LAN-only by convention.** Adding a new subdomain is an AdGuard rewrite (already wildcard-covered) + an Ingress with `host: <name>.kblab.me`. Do **not** attempt to add it to the public Cloudflare tunnel without a cross-repo Terraform change in `bnb/platform`.
- **Semaphore is retired.** Any mention of Semaphore UI in older docs is stale — the `ansible-runner` CronJob (`apps/ansible-runner/`) replaced it. No UI; trigger ad-hoc runs via `kubectl create job --from=cronjob/convergence-check`.
- **Most Phase B/D Ansible roles are authored but unbound.** They exist under `ansible/roles/` but the binding blocks in `site.yml` are commented out. Enabling a role = uncomment + seed vault + dry-run first.

## Where this doc fits

- `README.md` — maintainer entry point.
- `docs/README.md` — task-oriented runbook index.
- `docs/gitops.md` — layer split rules, Flux / SOPS operations.
- `docs/ansible.md` — host-layer roadmap + workflow.
- `docs/homelab-catalog.md` — flat service inventory.
- **`docs/architecture.md` (this doc)** — cross-layer walkthrough + failure domains + DR.
- `infrastructure.md` — snapshotted environment status (live-ish).
