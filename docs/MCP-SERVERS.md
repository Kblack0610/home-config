# MCP Servers for Home Cluster Management

This document describes the Model Context Protocol (MCP) servers configured for managing and monitoring the home cluster.

## Overview

MCP servers allow AI assistants (like Claude) to interact directly with your infrastructure. The `.mcp.json` file in this repository configures two servers:

1. **Kubernetes MCP** - Direct cluster management and monitoring
2. **Home Assistant MCP** - Smart home control and sensor data

## Prerequisites

### For Kubernetes MCP
- Node.js 18+ (for `npx`)
- `kubectl` configured with cluster access
- Kubeconfig at `~/.kube/config`

### For Home Assistant MCP
- Python 3.10+ with `uv` installed
- Home Assistant instance running
- Long-lived access token from Home Assistant

## Configuration

### Environment Variables

Set these environment variables before using the MCP servers:

```bash
# Home Assistant (required for home-assistant MCP)
export HOMEASSISTANT_URL="http://homeassistant.local:8123"
export HOMEASSISTANT_TOKEN="your-long-lived-access-token"
```

To get a Home Assistant token:
1. Go to your Home Assistant instance
2. Click your profile (bottom left)
3. Scroll to "Long-Lived Access Tokens"
4. Click "Create Token"
5. Copy and save the token securely

### Using with Claude Code

The `.mcp.json` file is automatically detected by Claude Code when working in this repository. Simply run `claude` from the repo root.

### Using with Claude Desktop

Copy the server configurations to your Claude Desktop config:

**Linux:** `~/.config/Claude/claude_desktop_config.json`
**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "kubernetes": {
      "command": "npx",
      "args": ["-y", "kubernetes-mcp-server@latest"]
    },
    "home-assistant": {
      "command": "uvx",
      "args": ["ha-mcp"],
      "env": {
        "HOMEASSISTANT_URL": "http://homeassistant.local:8123",
        "HOMEASSISTANT_TOKEN": "your-token-here"
      }
    }
  }
}
```

## Kubernetes MCP Server

**Source:** [containers/kubernetes-mcp-server](https://github.com/containers/kubernetes-mcp-server)

### Capabilities

| Toolset | Description |
|---------|-------------|
| `config` | Manage kubeconfig, list/switch contexts |
| `core` | Pod management, events, namespaces, logs |
| `helm` | Helm chart operations |

### Example Commands

Once configured, Claude can:
- List pods across namespaces
- Check deployment status
- View logs from any pod
- Describe resources
- Watch events
- Switch between cluster contexts

### Advanced Options

```bash
# Read-only mode (safer for production)
npx kubernetes-mcp-server@latest --read-only

# Disable destructive operations
npx kubernetes-mcp-server@latest --disable-destructive

# Specific toolsets only
npx kubernetes-mcp-server@latest --toolsets=core,config
```

## Home Assistant MCP Server

**Source:** [homeassistant-ai/ha-mcp](https://github.com/homeassistant-ai/ha-mcp)

### Capabilities

- Query entity states (sensors, switches, lights, etc.)
- Control devices
- Execute Home Assistant services
- Manage automations
- View system status
- Access history and statistics

### Example Use Cases

Once configured, Claude can:
- "What's the current temperature in the living room?"
- "Turn off all lights in the bedroom"
- "Show me the cluster CPU usage from Netdata sensors"
- "List all automations that are currently disabled"

## Security Considerations

### Kubernetes
- The MCP server uses your existing kubeconfig credentials
- Consider using `--read-only` for monitoring-only access
- Use `--disable-destructive` to prevent accidental deletions

### Home Assistant
- Store your token in environment variables, not in config files
- Create a dedicated user/token with limited permissions if desired
- The token grants full API access to Home Assistant

## Troubleshooting

### Kubernetes MCP not connecting

1. Verify kubeconfig: `kubectl cluster-info`
2. Check Node.js version: `node --version` (need 18+)
3. Test manually: `npx kubernetes-mcp-server@latest`

### Home Assistant MCP not connecting

1. Verify HA is accessible: `curl $HOMEASSISTANT_URL/api/`
2. Test token: `curl -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" $HOMEASSISTANT_URL/api/`
3. Check Python/uv: `uv --version`

## Alternative MCP Servers

Other options if needed:

### Kubernetes Alternatives
- [alexei-led/k8s-mcp-server](https://github.com/alexei-led/k8s-mcp-server) - Includes Helm, Istio, ArgoCD support
- [AWS EKS MCP](https://docs.aws.amazon.com/eks/latest/userguide/eks-mcp-introduction.html) - For EKS clusters

### Home Assistant Alternatives
- [allenporter/mcp-server-home-assistant](https://github.com/allenporter/mcp-server-home-assistant) - Being merged into HA Core
- Built-in HA MCP Server integration (HA 2025.2+)

## References

- [Model Context Protocol Specification](https://modelcontextprotocol.io/)
- [Kubernetes MCP Server Docs](https://github.com/containers/kubernetes-mcp-server)
- [Home Assistant MCP Docs](https://github.com/homeassistant-ai/ha-mcp)
- [Home Assistant API Docs](https://developers.home-assistant.io/docs/api/rest/)
