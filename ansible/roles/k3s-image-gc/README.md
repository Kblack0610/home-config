# k3s-image-gc

Sets kubelet's image garbage collection marks on the Pi k3s nodes (`pi_k3s`), through a k3s config drop-in, `/etc/rancher/k3s/config.yaml.d/40-image-gc.yaml`.

## Why

Kubelet removes unused images only past `image-gc-high-threshold`, 85% used by default. `NodeDiskLow` fires under 20% free, 80% used. On an SD-card Pi whose disk fills with old images, the node sat between the two with the alert firing and kubelet removing nothing: pi5-worker3 at 18% free on 2026-11-13, back to 47% after `sudo k3s crictl rmi --prune`. The marks here (75 and 70, `defaults/main.yml`) keep image GC under the alert.

## Apply

```bash
cd ansible && ansible-playbook playbooks/site.yml --limit pi_k3s --tags image-gc
```

A change restarts `k3s-agent` (or `k3s` on pi5-master). Its `KillMode=process` leaves running containers alone; on pi5-master the API is away for the restart.

## Verify

`sudo cat /etc/rancher/k3s/config.yaml.d/40-image-gc.yaml`, and `ps -o args= -C k3s-agent` or the kubelet log shows the two flags.
