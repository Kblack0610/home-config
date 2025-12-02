
  Open http://<NPM_IP>:81 for the admin panel.
  - Default login: admin@example.com / changeme

  ---
  Configuration Flow

  ┌──────────────────────────────────────────────────────────────────┐
  │  1. Configure OpenWrt DHCP                                       │
  │     Set DNS server to Pi 3 IP (e.g., 192.168.1.50)               │
  └──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  2. Configure AdGuard Home DNS Rewrites (on Pi 3)                │
  │     Filters → DNS Rewrites → Add:                                │
  │       homeassistant.yourdomain.com → <NPM_LOADBALANCER_IP>       │
  │       frigate.yourdomain.com       → <NPM_LOADBALANCER_IP>       │
  │       *.yourdomain.com             → <NPM_LOADBALANCER_IP>       │
  └──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  3. Configure Nginx Proxy Manager (on Cluster)                   │
  │     Hosts → Proxy Hosts → Add:                                   │
  │       Domain: homeassistant.yourdomain.com                       │
  │       Forward: <HOME_ASSISTANT_CLUSTER_IP>:8123                  │
  │       SSL: Request Let's Encrypt cert                            │
  └──────────────────────────────────────────────────────────────────┘

  Want me to delete the Kubernetes AdGuard Home manifests since you'll be running it on
