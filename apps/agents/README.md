# Agents

Reverse proxy for agent-web, the web view of the Claude agent sessions running in tmux on the desktop. From any LAN or tailnet device it lets you read a session's transcript or live screen, type into it, and answer the headless `agent-ask` queue.

The server runs on `pc-home-cachy-main` (`192.168.1.2:8790`) as the `agent-web` systemd user unit, because it needs the desktop's tmux sockets and `~/.claude`. Source: `~/.dotfiles/.local/src/agent-web` (unit in `~/.dotfiles-private`). The cluster only terminates TLS at `https://agents.kblab.me` and forwards.

## Access

- The Ingress carries `external-dns.alpha.kubernetes.io/controller: none`, so external-dns never publishes it and the wildcard tunnel cannot route it. Check: `dig +short agents.kblab.me @1.1.1.1` prints nothing.
- The `local-network-only` middleware allows RFC1918 sources only.
- agent-web signs you in with Forgejo (OAuth2, app `agents.kblab.me` on `git.kblab.me`) and only lets the logins in `AGENT_WEB_ALLOWED_USERS` through. A signed-out visit shows a Sign in with Forgejo button; there is no token to paste. Details: `~/.dotfiles/.local/src/agent-web/README.md`.

The desktop runs ufw; the allow rule for port 8790 from the LAN is the `desktop-firewall` role in `ansible/`.

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
curl -s -o /dev/null -w '%{http_code}\n' https://agents.kblab.me/api/agents   # 401 without a session
```

A `502` means Traefik cannot reach the desktop: check `systemctl --user status agent-web` and the ufw rule.
