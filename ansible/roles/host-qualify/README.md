# host-qualify

Installs the tools `scripts/host-qualify.py` runs on a host: stress-ng and fio for the burn-in, and smartmontools for the SMART check. The qualification itself runs from the workstation over ssh and writes `ansible/host_vars/<host>/qualification.yml`; see [docs/host-tiers.md](../../../docs/host-tiers.md).

```sh
ansible-playbook playbooks/site.yml --limit linux_bare_metal:pi_k3s --tags host-qualify --check --diff
ansible-playbook playbooks/site.yml --limit linux_bare_metal:pi_k3s --tags host-qualify
```

Bound to `linux_bare_metal` and `pi_k3s`. Add a group to the play before qualifying a host outside it.
