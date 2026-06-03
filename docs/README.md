# Documentation Index

Use this index to find the right document by task instead of scanning the repo tree manually.

## Core Operations

| Task | Document | Notes |
|------|----------|-------|
| Check environment health, inventory, ingress, backups | [../infrastructure.md](../infrastructure.md) | Live-ish reference document for current state |
| Trace a request or failure through all three layers | [architecture.md](./architecture.md) | Worked examples, GPU wiring, consumer map, failure domains, DR sequence |
| Deploy or troubleshoot GitOps changes | [gitops.md](./gitops.md) | Flux, SOPS, reconciliation, rollback |
| Add or remove an app from the cluster | [app-lifecycle.md](./app-lifecycle.md) | Step-by-step checklists, common pitfalls |
| Manage bare-metal host services (systemd, launchd, OpenWRT) | [ansible.md](./ansible.md) | Ansible inventory, vault, `ansible-runner` CronJob, phase roadmap |
| See what runs where (cluster vs bare-metal) | [homelab-catalog.md](./homelab-catalog.md) | One-page service → host → runtime → manager table |
| Verify backups or run a restore | [backup-runbook.md](./backup-runbook.md) | Backup schedules, manual triggers, restore flows |
| Inspect or edit files inside a running pod | [container-access.md](./container-access.md) | `kubectl debug` with nvim, cp round-trip, GitOps caveat |
| Reach a bare-metal node when SSH is broken (key mismatch, re-bootstrap) | [host-access.md](./host-access.md) | Four-naming-systems table, `kubectl debug node/...` escape hatch, SSH key re-bootstrap recipe |

## Environment and Platform Guides

| Task | Document | Notes |
|------|----------|-------|
| Provision or maintain Apple Silicon utility hosts | [mac-machines.md](./mac-machines.md) | CI runners, MLX inference, monitoring |
| Set up or operate Headscale clients and routing | [headscale-setup.md](./headscale-setup.md) | Complements the in-repo service manifests |
| Review finance stack design and integrations | [finance/README.md](./finance/README.md) | Entry point into finance-specific docs |
| Understand the finance platform in detail | [finance/finance-stack.md](./finance/finance-stack.md) | Architecture and service relationships |
| Work with local MCP server setup | [mcp-server.md](./mcp-server.md) | AI tooling and machine-local configuration notes |

## Service and Manifest Docs

Start with the service README in its manifest directory when you are editing or debugging a specific workload.

| Service | Path |
|---------|------|
| Home Assistant | [../apps/home-assistant/README.md](../apps/home-assistant/README.md) |
| Frigate | [../apps/frigate/README.md](../apps/frigate/README.md) |
| Headscale | [../apps/headscale/README.md](../apps/headscale/README.md) |
| LiteLLM | [../apps/litellm/README.md](../apps/litellm/README.md) |
| Forgejo | [../apps/forgejo/README.md](../apps/forgejo/README.md) |
| Monitoring | [../apps/monitoring/README.md](../apps/monitoring/README.md) |
| NAS | [../apps/nas/README.md](../apps/nas/README.md) |
| Pi3 AdGuard Home | [../apps/pi3-adguard-home/README.md](../apps/pi3-adguard-home/README.md) |
| Actual Budget | [../apps/actual-budget/README.md](../apps/actual-budget/README.md) |
| Actual Budget Tools | [../apps/actual-budget-tools/README.md](../apps/actual-budget-tools/README.md) |
| CrowdSec | [../apps/crowdsec/README.md](../apps/crowdsec/README.md) |
| Ansible Runner (CronJob) | [../apps/ansible-runner/README.md](../apps/ansible-runner/README.md) |
| Traefik | [../infrastructure/traefik/README.md](../infrastructure/traefik/README.md) |
| Ansible (host layer) | [../ansible/README.md](../ansible/README.md) |

## Documentation Conventions

- Root [../README.md](../README.md) is the maintainer entrypoint, not the full service catalog.
- Directory READMEs should stay short and operational:
  - overview
  - important manifests
  - config and secrets
  - deploy or run
  - verify
  - related docs
- Put long setup walkthroughs and restore procedures in `docs/` so service READMEs stay easy to scan.
- Ignore generated markdown under build output when assessing documentation coverage.
