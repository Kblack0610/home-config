# Headscale VPN Setup Guide

This guide covers the deployment and administration of Headscale, a self-hosted Tailscale control server for secure remote access to your home cluster.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    k3s Cluster (home-k3s)                   │
├─────────────────────────────────────────────────────────────┤
│  Headscale (control server)                                 │
│  ├── Web UI: https://headscale-ui.blackk.lan               │
│  ├── API: https://headscale.blackk.lan                     │
│  └── DERP/STUN: UDP 3478 (NAT traversal)                   │
│                                                             │
│  Subnet Router                                              │
│  └── Advertises: 10.42.0.0/16, 10.43.0.0/16               │
│                  (pod & service networks)                   │
└─────────────────────────────────────────────────────────────┘
              │ WireGuard tunnel
              ▼
┌─────────────────────────────────────────────────────────────┐
│  Remote Devices (phone, laptop, etc.)                       │
│  - Tailscale client connects to Headscale                   │
│  - MagicDNS: *.blackk.lan → cluster services               │
│  - Full access to K8s services without CA imports           │
└─────────────────────────────────────────────────────────────┘
```

## Prerequisites

- k3s cluster with Traefik ingress
- cert-manager with `letsencrypt` ClusterIssuer
- AdGuard Home for DNS (optional but recommended)

## Initial Deployment

### 1. Generate Secrets

First, generate the required secrets:

```bash
# Generate noise private key
openssl rand -hex 32
```

### 2. Update Secret Files

Edit `apps/headscale/secret.yaml` with your generated key:

```yaml
stringData:
  HEADSCALE_NOISE_PRIVATE_KEY: "your_generated_key_here"
```

### 3. Deploy Headscale

```bash
kubectl --context home-k3s apply -k apps/headscale/
```

### 4. Verify Deployment

```bash
# Check pods
kubectl --context home-k3s get pods -n headscale

# Check services
kubectl --context home-k3s get svc -n headscale

# Check ingress
kubectl --context home-k3s get ingress -n headscale

# Health check
curl -k https://headscale.blackk.lan/health
```

## User & Device Management

### Create a User

```bash
kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- \
  headscale users create myuser
```

### List Users

```bash
kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- \
  headscale users list
```

### Create Pre-auth Key

Generate a key for device registration:

```bash
# Single-use key (expires in 1 hour)
kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- \
  headscale preauthkeys create --user myuser --expiration 1h

# Reusable key (for multiple devices, expires in 30 days)
kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- \
  headscale preauthkeys create --user myuser --reusable --expiration 720h
```

### List Pre-auth Keys

```bash
kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- \
  headscale preauthkeys list --user myuser
```

### List Nodes (Registered Devices)

```bash
kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- \
  headscale nodes list
```

### Delete a Node

```bash
kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- \
  headscale nodes delete --identifier NODE_ID
```

## Client Setup

### Desktop (Linux/macOS/Windows)

1. Install Tailscale: https://tailscale.com/download

2. Connect to Headscale:
   ```bash
   tailscale up --login-server=https://headscale.blackk.lan --authkey=YOUR_PREAUTH_KEY
   ```

3. Or without pre-auth key (manual approval):
   ```bash
   tailscale up --login-server=https://headscale.blackk.lan
   ```
   Then register the node on the server:
   ```bash
   kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- \
     headscale nodes register --user myuser --key nodekey:XXXXXXXX
   ```

### Mobile (iOS/Android)

1. Install Tailscale from App Store / Play Store

2. Go to Settings → Account → Log out (if signed in)

3. Tap "Log in" then scroll down and tap "Use an alternate server"

4. Enter: `https://headscale.blackk.lan`

5. You'll get a registration URL. Run this on your server:
   ```bash
   kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- \
     headscale nodes register --user myuser --key nodekey:XXXXXXXX
   ```

## Subnet Router Setup

The subnet router allows tailnet devices to access K8s pod and service IPs.

### 1. Create Subnet Router User

```bash
kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- \
  headscale users create subnet-router
```

### 2. Generate Auth Key

```bash
kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- \
  headscale preauthkeys create --user subnet-router --reusable --expiration 8760h
```

### 3. Update Secret

Edit `apps/headscale/subnet-router.yaml` and replace `YOUR_PREAUTH_KEY_HERE` with the generated key, or create the secret directly:

```bash
kubectl --context home-k3s create secret generic tailscale-auth \
  -n headscale \
  --from-literal=TS_AUTHKEY='your_preauth_key_here' \
  --dry-run=client -o yaml | kubectl --context home-k3s apply -f -
```

### 4. Approve Subnet Routes

After the subnet router connects, approve its advertised routes:

```bash
kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- \
  headscale routes list

kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- \
  headscale routes enable --route ROUTE_ID
```

## Web UI Access

The Headscale UI provides a web interface for basic administration:

- URL: https://headscale-ui.blackk.lan
- Requires API key for authentication

Generate an API key:
```bash
kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- \
  headscale apikeys create --expiration 365d
```

## DNS Configuration

### MagicDNS

Headscale provides MagicDNS for devices on the tailnet:
- Base domain: `tail.blackk.lan`
- Each device gets: `hostname.tail.blackk.lan`

### Internal Service DNS

The Headscale config includes extra DNS records pointing `*.blackk.lan` to the Traefik ingress, so tailnet devices can access:
- home.blackk.lan
- grafana.blackk.lan
- openclaw.blackk.lan
- etc.

### AdGuard Integration

AdGuard Home (10.43.199.233) is configured as the upstream DNS for Headscale, allowing tailnet devices to resolve the same names as local network devices.

## Troubleshooting

### Check Headscale Logs

```bash
kubectl --context home-k3s logs -n headscale deployment/headscale -c headscale -f
```

### Check Subnet Router Logs

```bash
kubectl --context home-k3s logs -n headscale deployment/tailscale-subnet-router -f
```

### Verify DERP Server

The embedded DERP server helps devices behind NAT connect:

```bash
# Check if DERP is responding
curl -k https://headscale.blackk.lan/derp
```

### Device Can't Connect

1. Verify Headscale is healthy: `curl -k https://headscale.blackk.lan/health`
2. Check pre-auth key hasn't expired
3. Ensure firewall allows UDP 3478 for STUN

### Routes Not Working

1. List routes: `headscale routes list`
2. Enable routes: `headscale routes enable --route ROUTE_ID`
3. Check subnet router pod is running
4. Verify client has routes: `tailscale status`

### Certificate Issues

The ingress uses cert-manager with the `letsencrypt` ClusterIssuer (actually a self-signed CA). For devices on the tailnet, this works because traffic goes through the WireGuard tunnel and terminates at Traefik.

## Backup & Recovery

### Backup Database

```bash
kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- \
  cat /var/lib/headscale/db.sqlite > headscale-backup.sqlite
```

### Restore Database

```bash
kubectl --context home-k3s cp headscale-backup.sqlite \
  headscale/$(kubectl --context home-k3s get pod -n headscale -l app.kubernetes.io/name=headscale -o jsonpath='{.items[0].metadata.name}'):/var/lib/headscale/db.sqlite
```

## Security Considerations

- Pre-auth keys should be short-lived when possible
- Regularly rotate API keys
- Consider implementing ACL policies for fine-grained access control
- The subnet router has access to the entire cluster network; restrict its user's permissions if needed

## Useful Commands Reference

```bash
# Alias for convenience
alias hs='kubectl --context home-k3s exec -n headscale deployment/headscale -c headscale -- headscale'

# Then use:
hs users list
hs nodes list
hs preauthkeys create --user myuser --expiration 1h
hs routes list
hs routes enable --route 1
```

## Files Reference

| File | Purpose |
|------|---------|
| `apps/headscale/namespace.yaml` | Namespace definition |
| `apps/headscale/configmap.yaml` | Headscale configuration |
| `apps/headscale/secret.yaml` | Secret template (noise key) |
| `apps/headscale/pvc.yaml` | SQLite database storage |
| `apps/headscale/deployment.yaml` | Headscale + UI containers |
| `apps/headscale/service.yaml` | ClusterIP + LoadBalancer services |
| `apps/headscale/ingress.yaml` | Traefik ingress for HTTPS |
| `apps/headscale/subnet-router.yaml` | Tailscale subnet router |
| `apps/headscale/kustomization.yaml` | Kustomize manifest |
