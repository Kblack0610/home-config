# MCP Servers for Home Cluster Management

This document describes the Model Context Protocol (MCP) servers configured for managing and monitoring the home cluster.

## Overview

MCP servers allow AI assistants (like Claude) to interact directly with your infrastructure. The `.mcp.json` file in this repository configures five servers:

| Server | Purpose |
|--------|---------|
| **kubernetes** | Cluster management, pods, logs, deployments |
| **home-assistant** | Smart home control, sensors, automations |
| **ssh** | Direct shell access to cluster nodes |
| **prometheus** | Metrics queries and analysis |
| **grafana** | Dashboard management and visualization |

## Prerequisites

### General
- Node.js 18+ (for `npx` commands)
- Docker (for Prometheus MCP)

### Per-Server Requirements

| Server | Requirements |
|--------|-------------|
| kubernetes | `kubectl` configured, `~/.kube/config` |
| home-assistant | Python 3.10+, `uv` installed, HA token |
| ssh | SSH client, `~/.ssh/config` configured |
| prometheus | Docker, Prometheus instance running |
| grafana | Grafana 9.0+, service account token |

## Environment Variables

Create a `.env` file or export these variables:

```bash
# Home Assistant
export HOMEASSISTANT_URL="https://hass.kblab.me"
export HOMEASSISTANT_TOKEN="your-long-lived-access-token"

# Prometheus (if deployed)
export PROMETHEUS_URL="https://prometheus.kblab.me"

# Grafana (if deployed)
export GRAFANA_URL="https://grafana.kblab.me"
export GRAFANA_SERVICE_ACCOUNT_TOKEN="your-service-account-token"
```

---

## Server Details

### 1. Kubernetes MCP

**Source:** [containers/kubernetes-mcp-server](https://github.com/containers/kubernetes-mcp-server)

**Capabilities:**
- List/describe pods, deployments, services, configmaps
- View logs from any pod
- Check events and resource status
- Switch between cluster contexts
- Helm chart operations

**Advanced Options:**
```bash
# Read-only mode
npx kubernetes-mcp-server@latest --read-only

# Disable destructive operations
npx kubernetes-mcp-server@latest --disable-destructive

# Specific toolsets only
npx kubernetes-mcp-server@latest --toolsets=core,config
```

---

### 2. Home Assistant MCP

**Source:** [homeassistant-ai/ha-mcp](https://github.com/homeassistant-ai/ha-mcp)

**Capabilities:**
- Query entity states (sensors, switches, lights)
- Control devices
- Execute HA services
- Manage automations
- View history and statistics

**Getting a Token:**
1. Go to Home Assistant → Profile (bottom left)
2. Scroll to "Long-Lived Access Tokens"
3. Click "Create Token"
4. Copy and store securely

---

### 3. SSH MCP

**Source:** [AiondaDotCom/mcp-ssh](https://github.com/AiondaDotCom/mcp-ssh)

**Capabilities:**
- Execute commands on remote hosts
- File transfers via SCP
- Auto-discovers hosts from `~/.ssh/config`
- Supports SSH key authentication

**Requirements:**
- SSH client in PATH
- `~/.ssh/config` with your hosts configured
- SSH keys set up for passwordless auth

**Example `~/.ssh/config`:**
```
Host pi-master
    HostName 192.168.1.100
    User pi
    IdentityFile ~/.ssh/id_rsa

Host pi-worker1
    HostName 192.168.1.101
    User pi
    IdentityFile ~/.ssh/id_rsa
```

**Environment Variables:**
- `MCP_SILENT=true` - Disable debug output (default)
- `MCP_SILENT=false` - Enable debug logging

---

### 4. Prometheus MCP

**Source:** [pab1it0/prometheus-mcp-server](https://github.com/pab1it0/prometheus-mcp-server)

**Capabilities:**
- Query metrics using PromQL
- Analyze performance data
- Natural language metric queries
- Alert status checking

**Environment Variables:**
| Variable | Purpose | Required |
|----------|---------|----------|
| `PROMETHEUS_URL` | Server endpoint | Yes |
| `PROMETHEUS_USERNAME` | Basic auth user | No |
| `PROMETHEUS_PASSWORD` | Basic auth password | No |
| `PROMETHEUS_TOKEN` | Bearer token | No |

**Note:** Requires Docker. If you don't have Prometheus yet, consider deploying it via:
```bash
kubectl apply -f infrastructure/prometheus/  # (if you add it)
```

---

### 5. Grafana MCP

**Source:** [grafana/mcp-grafana](https://github.com/grafana/mcp-grafana)

**Capabilities:**
- Query dashboards
- Create/modify panels
- Execute datasource queries
- Manage alerts
- View annotations

**Creating a Service Account Token:**
1. Go to Grafana → Administration → Service Accounts
2. Create a new service account
3. Add a token to the service account
4. Assign "Editor" role (or custom RBAC)
5. Copy the token

**Advanced Options:**
```bash
# Read-only mode
npx mcp-grafana@latest --disable-write

# Disable admin operations
npx mcp-grafana@latest --disable-admin
```

---

## Usage

### With Claude Code

The `.mcp.json` file is automatically detected when running `claude` from this repo directory.

### With Claude Desktop

Copy configurations to your Claude Desktop config:

**Linux:** `~/.config/Claude/claude_desktop_config.json`
**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

---

## Security Considerations

### Kubernetes
- Uses your existing kubeconfig credentials
- Consider `--read-only` for monitoring-only access
- Use `--disable-destructive` to prevent deletions

### Home Assistant
- Store token in environment variables, not config files
- Create dedicated user/token with limited permissions if desired
- Token grants full API access

### SSH
- Uses your existing SSH keys and config
- Only hosts in `~/.ssh/config` are accessible
- Consider restricting which hosts are configured

### Prometheus/Grafana
- Use service accounts with minimal required permissions
- Consider read-only tokens for monitoring
- Don't commit tokens to git

---

## Adding New Services

If you deploy additional observability tools, consider these MCP servers:

| Service | MCP Server |
|---------|-----------|
| Loki (logs) | [grafana/loki-mcp](https://github.com/grafana/loki-mcp) |
| Tempo (traces) | [grafana/tempo-mcp-server](https://github.com/grafana/tempo-mcp-server) |
| Alertmanager | [ntk148v/alertmanager-mcp-server](https://github.com/ntk148v/alertmanager-mcp-server) |

---

## Troubleshooting

### Kubernetes MCP not connecting
```bash
# Verify kubeconfig
kubectl cluster-info

# Test manually
npx kubernetes-mcp-server@latest
```

### Home Assistant MCP not connecting
```bash
# Test HA API
curl -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" \
  "$HOMEASSISTANT_URL/api/"
```

### SSH MCP not finding hosts
```bash
# Verify SSH config
cat ~/.ssh/config

# Test SSH manually
ssh pi-master hostname
```

### Prometheus MCP not connecting
```bash
# Test Prometheus API
curl "$PROMETHEUS_URL/api/v1/status/config"

# Check Docker
docker run --rm ghcr.io/pab1it0/prometheus-mcp-server:latest --help
```

### Grafana MCP not connecting
```bash
# Test Grafana API
curl -H "Authorization: Bearer $GRAFANA_SERVICE_ACCOUNT_TOKEN" \
  "$GRAFANA_URL/api/org"
```

---

## References

- [Model Context Protocol Specification](https://modelcontextprotocol.io/)
- [Kubernetes MCP Server](https://github.com/containers/kubernetes-mcp-server)
- [Home Assistant MCP](https://github.com/homeassistant-ai/ha-mcp)
- [SSH MCP](https://github.com/AiondaDotCom/mcp-ssh)
- [Prometheus MCP Server](https://github.com/pab1it0/prometheus-mcp-server)
- [Grafana MCP Server](https://github.com/grafana/mcp-grafana)
