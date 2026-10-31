# Move a local-path PVC to another node

A local-path PV is pinned to one node, and everything on it goes down with that node's disk. On 2026-10-31 hp-victus hit DiskPressure and took 13 PVs with it, including the pager.

## Procedure

1. Open a PR adding `nodeSelector` to the Deployment. Wait for CI, and do not merge yet.
2. Suspend Flux: `kubectl -n flux-system patch kustomization apps --type=merge -p '{"spec":{"suspend":true}}'`
3. For each app: `KUBE_CONTEXT=home-k3s scripts/move-local-path-pvc.sh <ns> <pvc> <deployment> <node>`
4. Merge the PR, then resume: `kubectl -n flux-system patch kustomization apps --type=merge -p '{"spec":{"suspend":false}}'`

Merge before resuming. Otherwise Flux removes the `nodeSelector`, and the app can land on the wrong node.

The script sets the old PV to `Retain`, stops the app, recreates the PVC on the target node, streams the data between two pods, compares hashes and restarts the app. Check the image supports the target's architecture first: Pis are arm64.

## Rollback

The old data stays on the old node until you remove it.

1. Scale the Deployment to 0 and delete the new PVC.
2. Clear the old PV's claim: `kubectl patch pv <pv> --type=json -p '[{"op":"remove","path":"/spec/claimRef"}]'`
3. Recreate the PVC with `spec.volumeName: <pv>`, restore the old `nodeSelector` and scale back up.

Anything written after the move is lost.

## Cleanup

After a week, set the old PV's reclaim policy back to `Delete` and delete the PV. The provisioner then removes the directory.
