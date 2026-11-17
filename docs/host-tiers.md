# Host tiers

`host_tier` in [the inventory](../ansible/inventory.yml) records each host's placement tier. P1 assignments are provisional: `asus-laptop` is `core` under the accepted mains and battery-alert exception, `hp-victus` is `probation`, and other hosts are `standard`. Qualification records and qualification-based admission belong to P2 and P5 of the host-tiers plan.

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
| New pod is Pending after an affinity change | Confirm every intended node has its inventory tier label and inspect the pod's scheduling event. |
