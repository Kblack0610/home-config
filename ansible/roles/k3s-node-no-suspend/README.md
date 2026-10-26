# k3s-node-no-suspend

Prevents laptop k3s nodes and CI hosts from suspending. Bound to `hp-victus`, `asus-laptop` and `razer-laptop` in `ansible/playbooks/site.yml`.

## Why this exists

A k3s agent that suspends silently disappears from the cluster's view (`kubectl get nodes` shows `Ready=Unknown` for the node, indefinitely). PVCs pinned to that node become unreachable, deployments using its GPU stop scheduling, and the cluster can't reconcile around it.

This actually happened on 2026-05-07: `hp-victus`'s lid was closed accidentally during unrelated work. Systemd-logind suspended the machine. Two weeks later, four cluster workloads were still showing `Terminating` pods stuck on that node (comfyui, openclaw, prometheus, plus the cloudflared tunnel) because the kubelet wasn't there to drain them. Bringing hp-victus back online resolved everything, but the gap had already caused a Flux reconciliation stall.

This role makes that failure mode impossible.

## What it does

1. Writes `/etc/systemd/logind.conf.d/k3s-no-suspend.conf` setting `HandleLidSwitch=ignore`, `HandleSuspendKey=ignore`, `IdleAction=ignore`, etc.
2. Masks `sleep.target`, `suspend.target`, `hibernate.target`, `hybrid-sleep.target` so `systemctl suspend` can't be invoked even manually.
3. Restarts `systemd-logind` to pick up the new config.
4. Verifies the live config matches expectation.

The settings are one dict, `k3s_no_suspend_logind` in `defaults/main.yml`, which writes the dropin and drives both live checks. `k3s_no_suspend_power_key` (default `poweroff`) sets what a short press of the power key does; razer-laptop sets `ignore` because its power button is a keyboard key and it sits stacked with the lid shut (a press powered it off on 2026-10-24). Holding the button still forces power-off.

## Apply

```bash
ansible-playbook -i ansible/inventory.yml ansible/playbooks/site.yml --limit hp-victus,asus-laptop --tags no-suspend
# or via the in-cluster runner:
kubectl create job --from=cronjob/convergence-check -n ansible-runner manual-$(date +%s)
```

## Unapply (recover full sleep behavior)

```bash
sudo rm /etc/systemd/logind.conf.d/k3s-no-suspend.conf
sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target
sudo systemctl restart systemd-logind
```
