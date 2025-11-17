# Home Configuration

Configuration for home automation services including Home Assistant and Frigate.

## Contents

- **[home-assistant/](./home-assistant/)** - Home Assistant deployment on Kubernetes
  - Helm-based installation with multiple configuration options
  - Support for USB devices (Zigbee/Z-Wave)
  - Ingress and LoadBalancer configurations
  - Optimized for k3s clusters

- **[frigate/](./frigate/)** - Frigate NVR configuration
  - Docker Compose setup
  - Network video recorder with object detection

## Quick Start

### Home Assistant on Kubernetes

```bash
cd home-assistant
./install.sh
```

Or manually:
```bash
helm repo add pajikos https://pajikos.github.io/home-assistant-helm-chart/
helm repo update
kubectl create namespace home-assistant
helm install home-assistant pajikos/home-assistant \
  --namespace home-assistant \
  -f home-assistant/values.yaml
```

See [home-assistant/README.md](./home-assistant/README.md) for detailed instructions.

### Frigate

```bash
cd frigate
docker-compose up -d
```

## Integration

Once both services are running, you can integrate Frigate into Home Assistant:
1. In Home Assistant, go to Settings → Devices & Services
2. Add the Frigate integration
3. Point it to your Frigate instance

## Documentation

- [Home Assistant Documentation](https://www.home-assistant.io/docs/)
- [Frigate Documentation](https://docs.frigate.video/)
