# Home Assistant on Kubernetes

This directory contains the configuration for running Home Assistant on a local Kubernetes cluster.

## Prerequisites

Before installing Home Assistant, ensure you have:

- **Kubernetes cluster** running (k3s, k8s, etc.)
- **Helm 3** installed on your machine
- **kubectl** configured to access your cluster
- **StorageClass** available for persistent volumes (check with `kubectl get sc`)
- **MetalLB** or similar LoadBalancer (recommended for easy access)

## Quick Start

### 1. Add the Helm Repository

```bash
helm repo add pajikos https://pajikos.github.io/home-assistant-helm-chart/
helm repo update
```

### 2. Customize values.yaml

Edit `values.yaml` and update at minimum:

- `env.TZ` - Your timezone
- `service.main.type` - How you want to access Home Assistant (LoadBalancer recommended)
- `persistence.config.size` - Storage size for your config

If using Ingress or reverse proxy:
- Update the `config.http.trusted_proxies` section with your cluster's network CIDRs

To find your cluster's network CIDRs:
```bash
# For k3s:
kubectl cluster-info dump | grep -m 1 service-cluster-ip-range
kubectl cluster-info dump | grep -m 1 cluster-cidr

# For standard k8s, check your CNI configuration
```

### 3. Create Namespace and Install

```bash
# Create namespace
kubectl create namespace home-assistant

# Install Home Assistant
helm install home-assistant pajikos/home-assistant \
  --namespace home-assistant \
  -f values.yaml
```

### 4. Check Status

```bash
# Watch pod startup
kubectl get pods -n home-assistant -w

# Check logs
kubectl logs -n home-assistant -l app.kubernetes.io/name=home-assistant -f

# Get service access information
kubectl get svc -n home-assistant
```

### 5. Access Home Assistant

If using **LoadBalancer**:
```bash
kubectl get svc -n home-assistant home-assistant
# Access at http://<EXTERNAL-IP>:8123
```

If using **NodePort**:
```bash
# Access at http://<any-node-ip>:<nodePort>
```

If using **Ingress**:
```bash
# Access at your configured hostname (e.g., https://home.example.com)
```

On first access, you'll be prompted to create an admin account.

## Updating Home Assistant

### Update to Latest Version

```bash
# Update Helm repo
helm repo update

# Upgrade installation
helm upgrade home-assistant pajikos/home-assistant \
  --namespace home-assistant \
  -f values.yaml
```

### Pin to Specific Version

Edit `values.yaml` and set:
```yaml
image:
  tag: "2024.11.0"
```

Then upgrade:
```bash
helm upgrade home-assistant pajikos/home-assistant \
  --namespace home-assistant \
  -f values.yaml
```

## USB Device Support (Zigbee/Z-Wave)

If you need to connect USB devices like Zigbee or Z-Wave sticks, see the detailed guide in `usb-device-values.yaml`.

Quick summary:
1. Label the node with the USB device: `kubectl label node <node-name> hardware.homeassistant=usb-devices`
2. Use the configuration in `usb-device-values.yaml`
3. Upgrade your deployment

**Note**: The pod will be pinned to the specific node where the USB device is connected.

## Integration with Frigate

Since Frigate is already running (in `../frigate/`), you can easily integrate it with Home Assistant:

### Option 1: Frigate Integration (Recommended)

1. In Home Assistant, go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for "Frigate"
4. Enter Frigate's URL:
   - If Frigate is on the same machine: `http://<host-ip>:5000`
   - If you migrate Frigate to Kubernetes: `http://frigate.frigate.svc.cluster.local:5000`

### Option 2: MQTT Integration

If Frigate is configured with MQTT, both can share the same MQTT broker:

1. Deploy an MQTT broker (like Mosquitto) in Kubernetes
2. Configure Frigate to use it
3. Configure Home Assistant to use the same broker
4. Frigate will auto-publish camera events

## Backup and Restore

### Backup

Your entire Home Assistant configuration is in the persistent volume. To backup:

```bash
# Get the PVC name
kubectl get pvc -n home-assistant

# Create a backup pod and copy data
kubectl run backup --rm -i --tty \
  --image=alpine \
  --namespace=home-assistant \
  --overrides='
{
  "spec": {
    "containers": [{
      "name": "backup",
      "image": "alpine",
      "command": ["sleep", "3600"],
      "volumeMounts": [{
        "name": "config",
        "mountPath": "/config"
      }]
    }],
    "volumes": [{
      "name": "config",
      "persistentVolumeClaim": {
        "claimName": "config-home-assistant-0"
      }
    }]
  }
}
'

# In another terminal, copy the data
kubectl cp home-assistant/backup:/config ./home-assistant-backup
```

### Restore

To restore from backup:

1. Install Home Assistant fresh (it will create a new PVC)
2. Scale down the Home Assistant pod: `kubectl scale statefulset home-assistant -n home-assistant --replicas=0`
3. Copy backup data to PVC (reverse of backup process)
4. Scale back up: `kubectl scale statefulset home-assistant -n home-assistant --replicas=1`

## Troubleshooting

### Pod won't start

Check logs:
```bash
kubectl logs -n home-assistant <pod-name>
kubectl describe pod -n home-assistant <pod-name>
```

Common issues:
- **PVC not bound**: Check if you have a StorageClass: `kubectl get sc`
- **Image pull errors**: Check internet connectivity from cluster
- **USB device not found**: Ensure device is plugged into the correct node

### Can't access Web UI

Check service:
```bash
kubectl get svc -n home-assistant
```

If using Ingress, check:
```bash
kubectl get ingress -n home-assistant
kubectl describe ingress -n home-assistant home-assistant
```

### "Login attempt failed" with reverse proxy

You need to configure trusted proxies. Edit `values.yaml`:
```yaml
config:
  http:
    use_x_forwarded_for: true
    trusted_proxies:
      - 10.42.0.0/16  # Your pod network CIDR
      - 10.43.0.0/16  # Your service network CIDR
```

Then upgrade:
```bash
helm upgrade home-assistant pajikos/home-assistant \
  --namespace home-assistant \
  -f values.yaml
```

## Uninstall

To completely remove Home Assistant:

```bash
# Delete Helm release
helm uninstall home-assistant --namespace home-assistant

# Delete PVC (WARNING: This deletes all your data!)
kubectl delete pvc -n home-assistant --all

# Delete namespace
kubectl delete namespace home-assistant
```

## Resources

- [Home Assistant Documentation](https://www.home-assistant.io/docs/)
- [pajikos Helm Chart](https://github.com/pajikos/home-assistant-helm-chart)
- [Home Assistant Community Forums](https://community.home-assistant.io/)
- [Kubernetes Deployments Discussion](https://community.home-assistant.io/t/home-assistant-on-kubernetes/341665)

## Notes

- **Add-ons are not supported** in Kubernetes deployments. You'll need to install equivalents (MQTT, Node-RED, etc.) as separate Kubernetes applications.
- Home Assistant will run as a single replica. For high availability, you'd need a more complex setup with shared storage.
- Updates are manual - Home Assistant won't auto-update like Home Assistant OS does.
