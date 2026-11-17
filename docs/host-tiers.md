# Host tiers

`host_tier` in [the inventory](../ansible/inventory.yml) records each host's placement tier. P1 assignments are provisional: `asus-laptop` is `core` under the accepted mains and battery-alert exception, `hp-victus` is `probation`, and other hosts are `standard`. A host earns a tier through a [qualification record](#qualification); P5 of the host-tiers plan reassigns every host from those records.

| Tier | Work |
|---|---|
| `core` | Irreplaceable single-writer state; no CI, GPU work or load tests |
| `standard` | Ordinary apps, backed-up or rebuildable state, and CI |
| `probation` | Scratch, load tests and eligible CI; no persistent volumes |

`no_ci_hosts` remains an independent prohibition. hp-victus must never be core and must never run CI. Ask `vy3RRu`, answer `a`, preserves its explicit exclusion until another canonical eligibility rule replaces it. `check-runner-roster.py` checks both exclusions and requires the Forgejo runner to exclude core tiers.

## Node labels

The `node-labels` Ansible role owns `kblab.me/tier` and `kblab.me/game-server` on the `k3s_nodes` group. It replaces the game-server-only role and resolves the actual node name from the inventory IP, including Pi aliases. The role removes a stale game-server label when a host leaves `game_server_nodes`.

Run from `ansible/`, using the collections installed per its README:

```sh
ansible-playbook playbooks/site.yml --tags node-labels --limit k3s_nodes --check --diff
ansible-playbook playbooks/site.yml --tags node-labels --limit k3s_nodes
kubectl --context home-k3s get nodes -L kblab.me/tier
```

Apply labels before merging a scheduling restriction that reads them. Labels and `requiredDuringSchedulingIgnoredDuringExecution` affinity do not evict running pods, but a Deployment template change starts a rollout. Existing hostname pins and PV bindings stay in place until the later migration phases. The repeated freqtrade hp-victus exclusions use `tier NotIn [probation]`; the separate razer-laptop exclusion stays where it existed. Kubernetes `NotIn` also matches an absent label, so node labelling is a rollout prerequisite.

## Qualification

`scripts/host-qualify.py <host>` checks a host against each tier's bar and writes `ansible/host_vars/<host>/qualification.yml`, naming the highest tier whose every check passed. Only the script writes that file; commit it in a PR. The tools it runs on the host come from `roles/host-qualify` (`linux_bare_metal`).

| Check | Source | Gates |
|---|---|---|
| Headroom ROOM | `host-headroom.py`, 7 days | core |
| No kernel fault in 14 days | Loki rule group `host-kernel-faults`, as `host-postmortem.py` reads it | standard, core |
| Uptime 14 days | `node_boot_time_seconds` | core |
| On mains, or no battery | `node_power_supply_online` | core |
| Watchdog armed | `RuntimeWatchdogUSec` and `/dev/watchdog0` | standard, core |
| netconsole active | the `netconsole` unit | core |
| SMART | `smartctl -H -A` on each NVMe, SATA and SAS disk: passed, no critical warning, no media errors | standard, core |
| Burn-in | stress-ng on every CPU plus 25% of memory with `--verify` (10 minutes by default), a verified 1 GiB fio random write, no fault-rule kernel line, peak CPU temperature at most 90C | standard, core |

The burn-in runs only when the passive checks meet the standard bar, so a host with a fault history is not stressed. It loads the whole host: cordon a host first if its slowed workloads matter. A host holding core state takes `--no-burn-in`, which records the passive checks and cannot qualify; qualify it before it takes that state. `--dry-run` prints the record without writing it.

`check-placement.py` refuses a `core` host with no record, with a record for a lower tier, or with a record older than 30 days. The rule ships in report mode with the rest of the check until P5.

## Placement check

Stateful native workloads declare `metadata.annotations.kblab.me/criticality` as `high` or `standard`. High includes finance, Forgejo, identity state, Home Assistant, primary databases and photo originals. Rebuildable state and backed-up application data are standard. Declare the annotation on a workload rather than its pod template when placement is unchanged.

High criticality requires `nodeSelector: {kblab.me/tier: core}` or a required node affinity expression `kblab.me/tier In [core]` in every OR term. A hostname pin does not substitute for this restriction. A contradictory pin to a non-core or unknown host is also reported. The checker follows local Kustomization resources from the Flux `apps` entry point and checks PVCs, claim templates and application hostPaths. System mounts used by node agents are excluded.

Helm storage owners with explicit persistence or storage values use component `podAnnotations` (monitoring) or inherit the HelmRelease annotation (Crowdsec and Langfuse). Chart defaults, generated chart resources and Kustomize patches still require rendered review; the source checker cannot infer persistence hidden in a chart. Monitoring's plain-Helm values are checked but do not deploy through Flux.

```sh
python3 scripts/check-placement.py --report
python3 scripts/check-placement.py
python3 scripts/test_check_placement.py
```

The `fleet-roster` CI job uses report mode until P5 clears existing placement violations. Strict mode returns 1 for policy violations; malformed input and an empty workload set return 2 in either mode. Tests include a planted high-criticality workload on a standard node and assert both the failing result and its diagnostic. P1 does not move high-criticality services or claim their existing placements satisfy the future qualification bar.

| Symptom | Action |
|---|---|
| Node alias cannot be labelled | Check the inventory IP against node InternalIP; the role requires exactly one match. |
| Delegated kubectl cannot read the server kubeconfig | The role's kubectl tasks must become root on the server; retain the kubeconfig's restricted permissions. |
| Role module cannot resolve an installed collection | Include the documented control-node collection directory in `ANSIBLE_COLLECTIONS_PATH`; do not copy collections into a worktree. |
| High-criticality placement is reported | Keep the finding for P5's move list; qualify a destination before changing the workload's pin or PV. |
| `host-qualify` says the host lacks stress-ng, fio or smartctl | Apply `roles/host-qualify` to it (add its group to that play first if it is not in `linux_bare_metal`). |
| Applying `roles/host-qualify` stops at `community.general.pacman` not resolved | Run the playbook with `ANSIBLE_COLLECTIONS_PATH` pointing at the installed collections, as for `node-labels`. |
| A core host is reported with no fresh qualification | Run `scripts/host-qualify.py <host>` (cordon it if its workloads matter during the burn-in) and commit the record. |
| New pod is Pending after an affinity change | Confirm every intended node has its inventory tier label and inspect the pod's scheduling event. |
