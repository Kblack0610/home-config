# Agents

Reverse proxy for agent-web, the web view of the Claude agent sessions running in tmux on the desktop. From any LAN or tailnet device it lets you read a session's transcript or live screen, type into it, and answer the headless `agent-ask` queue.

The server runs on `pc-home-cachy-main` (`192.168.1.2:8790`) as the `agent-web` systemd user unit, because it needs the desktop's tmux sockets and `~/.claude`. Source: `~/.dotfiles/.local/src/agent-web` (unit in `~/.dotfiles-private`). The cluster only terminates TLS at `https://agents.kblab.me` and forwards.

## Access

- The Ingress carries `external-dns.alpha.kubernetes.io/controller: none`, so external-dns never publishes it and the wildcard tunnel cannot route it. Check: `dig +short agents.kblab.me @1.1.1.1` prints nothing.
- The `local-network-only` middleware allows RFC1918 sources only.
- agent-web checks a token. On the desktop it is in `~/.config/agent-web/token`. Open `https://agents.kblab.me/?token=<token>` once per browser; the page trades it for a cookie.

The desktop runs ufw, so port 8790 has to be allowed from the LAN: `sudo ufw allow from 192.168.1.0/24 to any port 8790 proto tcp`.

## Files

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles the namespace, service/endpoints, and ingress |
| `namespace.yaml` | Creates the `agents` namespace |
| `service.yaml` | ClusterIP service plus manual endpoints for the desktop's LAN IP |
| `ingress.yaml` | TLS ingress for `agents.kblab.me`, restricted to local-network source ranges |

## Verify

```bash
kubectl --context home-k3s -n agents get svc,endpoints,ingress
curl -s https://agents.kblab.me/healthz            # ok
curl -s -o /dev/null -w '%{http_code}\n' https://agents.kblab.me/api/agents   # 401 without the token
```

A `502` means Traefik cannot reach the desktop: check `systemctl --user status agent-web` and the ufw rule.
