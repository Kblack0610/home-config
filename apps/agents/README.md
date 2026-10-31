# Agents

Reverse proxy for agent-web, the web view of the Claude agent sessions running in tmux on the desktop. From any browser, on or off the LAN, it lets you read a session's transcript or live screen, type into it, and answer the headless `agent-ask` queue.

The server runs on `pc-home-cachy-main` (`192.168.1.2:8790`) as the `agent-web` systemd user unit, because it needs the desktop's tmux sockets and `~/.claude`. Source: `~/.dotfiles/.local/src/agent-web` (unit in `~/.dotfiles-private`). The cluster only terminates TLS at `https://agents.kblab.me` and forwards.

## Access

- Public on purpose, so a phone can reach it off the LAN without a VPN: external-dns publishes the host and the wildcard tunnel routes it. Check: `dig +short agents.kblab.me @1.1.1.1` prints Cloudflare addresses. TLS ends at Cloudflare, so transcripts and typed text are visible at its edge.
- agent-web signs you in with Forgejo (OAuth2, app `agents.kblab.me` on `git.kblab.me`) and only lets the logins in `AGENT_WEB_ALLOWED_USERS` through. A signed-out visit shows a Sign in with Forgejo button; there is no token to paste. Details: `~/.dotfiles/.local/src/agent-web/README.md`.
- This page can type into live agent sessions, so the Forgejo account must keep 2FA on. Check: the 2FA column of `forgejo admin user list` in the Forgejo pod.
- The `crowdsec-bouncer` middleware sits in front. `local-network-only` would do nothing here, because tunnel traffic arrives from a cluster IP (`apps/gatus-fleet/ingress.yaml`).

The desktop runs ufw; the allow rule for port 8790 from the LAN is the `desktop-firewall` role in `ansible/`.

## Files

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles the namespace, service/endpoints, and ingress |
| `namespace.yaml` | Creates the `agents` namespace |
| `service.yaml` | ClusterIP service plus manual endpoints for the desktop's LAN IP |
| `ingress.yaml` | TLS ingress for `agents.kblab.me`, published through the tunnel, behind crowdsec |

## Verify

```bash
kubectl --context home-k3s -n agents get svc,endpoints,ingress
curl -s https://agents.kblab.me/healthz            # ok
curl -s -o /dev/null -w '%{http_code}\n' https://agents.kblab.me/api/agents   # 401 without a session
```

A `502` means Traefik cannot reach the desktop: check `systemctl --user status agent-web` and the ufw rule.
