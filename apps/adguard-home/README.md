  apps/adguard-home/
  ├── kustomization.yaml
  ├── namespace.yaml
  ├── pvc.yaml          # 1Gi for config, 2Gi for work data
  ├── deployment.yaml   # Using adguard/adguardhome:latest
  └── service.yaml      # LoadBalancer for DNS (53) + Web UI (3000/80/443)

  Deploy

  kubectl apply -k /home/kblack0610/dev/home-config/apps/adguard-home/

  After Deployment

  1. Get the LoadBalancer IP:
  kubectl get svc -n adguard-home
  2. Initial Setup: Open http://<EXTERNAL-IP>:3000 in your browser to complete the setup wizard.
  3. Configure your router to use the LoadBalancer IP as the DNS server for your network, OR set it per-device.

  Key Points

  | Port         | Purpose                |
  |--------------|------------------------|
  | 53 (TCP/UDP) | DNS queries            |
  | 3000         | Initial setup wizard   |
  | 80           | Web UI after setup     |
  | 443          | HTTPS / DNS-over-HTTPS |
  | 853          | DNS-over-TLS           |

  The deployment uses LoadBalancer service type which works with k3s's built-in servicelb (Klipper). Your devices can point their
  DNS to the assigned external IP.

  Want me to deploy it now, or would you prefer to review the manifests first?


