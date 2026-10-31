#!/usr/bin/env bash
# Move a Deployment's local-path PVC to another node, keeping its data.
#
#   KUBE_CONTEXT=home-k3s scripts/move-local-path-pvc.sh <ns> <pvc> <deployment> <target-node>
#
# Suspend Flux first, or it recreates the PVC mid-move and the app starts empty:
#   kubectl -n flux-system patch kustomization apps --type=merge -p '{"spec":{"suspend":true}}'
# Then merge the matching nodeSelector before resuming. Procedure, rollback and
# cleanup: docs/runbooks/move-local-path-pvc.md
set -euo pipefail

[ $# -eq 4 ] || { sed -n 2,8p "$0"; exit 2; }
NS=$1 PVC=$2 DEPLOY=$3 NODE=$4
K=(kubectl ${KUBE_CONTEXT:+--context "$KUBE_CONTEXT"})
IMAGE=alpine:3.24.1
SRC=pvc-move-src DST=pvc-move-dst

[ "$("${K[@]}" -n flux-system get kustomization apps -o jsonpath='{.spec.suspend}')" = true ] \
  || { echo "ERROR: Flux kustomization apps is not suspended" >&2; exit 1; }

PV=$("${K[@]}" -n "$NS" get pvc "$PVC" -o jsonpath='{.spec.volumeName}')
PV_JSON=$("${K[@]}" get pv "$PV" -o json)
OLD_NODE=$(jq -r '.spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[0].values[0]' <<<"$PV_JSON")
OLD_PATH=$(jq -r '.spec.local.path // .spec.hostPath.path' <<<"$PV_JSON")
[ "$OLD_NODE" != "$NODE" ] || { echo "ERROR: $PVC is already on $NODE" >&2; exit 1; }
REPLICAS=$("${K[@]}" -n "$NS" get deploy "$DEPLOY" -o jsonpath='{.spec.replicas}')
SELECTOR=$("${K[@]}" -n "$NS" get deploy "$DEPLOY" -o json \
  | jq -r '.spec.selector.matchLabels | to_entries | map("\(.key)=\(.value)") | join(",")')
NEW_PVC=$("${K[@]}" -n "$NS" get pvc "$PVC" -o json | jq '{apiVersion,kind,
  metadata:{name:.metadata.name,namespace:.metadata.namespace,labels:(.metadata.labels // {})},
  spec:{accessModes:.spec.accessModes,storageClassName:.spec.storageClassName,resources:.spec.resources}}')
echo "Moving $NS/$PVC ($PV): $OLD_NODE -> $NODE"

cleanup() { "${K[@]}" -n "$NS" delete pod "$SRC" "$DST" --ignore-not-found --wait=false >/dev/null 2>&1 || true; }
trap cleanup EXIT

# Deleting the PVC must not delete the data: the rollback depends on this.
"${K[@]}" patch pv "$PV" --type=merge -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}' >/dev/null
[ "$("${K[@]}" get pv "$PV" -o jsonpath='{.spec.persistentVolumeReclaimPolicy}')" = Retain ] \
  || { echo "ERROR: could not set $PV to Retain" >&2; exit 1; }

"${K[@]}" -n "$NS" scale deploy "$DEPLOY" --replicas=0 >/dev/null
"${K[@]}" -n "$NS" wait --for=delete pod -l "$SELECTOR" --timeout=120s >/dev/null 2>&1 || true
"${K[@]}" -n "$NS" delete pvc "$PVC" --timeout=60s >/dev/null
"${K[@]}" apply -f - <<<"$NEW_PVC" >/dev/null

"${K[@]}" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: {name: $SRC, namespace: $NS}
spec:
  restartPolicy: Never
  nodeSelector: {kubernetes.io/hostname: $OLD_NODE}
  containers:
    - {name: c, image: $IMAGE, command: [sleep, "3600"], volumeMounts: [{name: v, mountPath: /v, readOnly: true}]}
  volumes:
    - {name: v, hostPath: {path: "$OLD_PATH", type: Directory}}
---
apiVersion: v1
kind: Pod
metadata: {name: $DST, namespace: $NS}
spec:
  restartPolicy: Never
  nodeSelector: {kubernetes.io/hostname: $NODE}
  containers:
    - {name: c, image: $IMAGE, command: [sleep, "3600"], volumeMounts: [{name: v, mountPath: /v}]}
  volumes:
    - {name: v, persistentVolumeClaim: {claimName: $PVC}}
YAML
"${K[@]}" -n "$NS" wait --for=condition=Ready pod "$SRC" "$DST" --timeout=180s >/dev/null

# Streamed between pods, so a vault's contents never land on this machine.
"${K[@]}" -n "$NS" exec "$SRC" -- tar -C /v --numeric-owner -cf - . \
  | "${K[@]}" -n "$NS" exec -i "$DST" -- tar -C /v --numeric-owner -xpf -

SUM='cd /v && find . -type f -print0 | sort -z | xargs -0 -r sha256sum | sha256sum; find . -type f | wc -l'
A=$("${K[@]}" -n "$NS" exec "$SRC" -- sh -c "$SUM")
B=$("${K[@]}" -n "$NS" exec "$DST" -- sh -c "$SUM")
[ "$A" = "$B" ] || { echo "ERROR: copy differs. Old data is intact at $OLD_NODE:$OLD_PATH" >&2; exit 1; }
echo "Copy verified: $(tail -1 <<<"$A") files"

cleanup
"${K[@]}" -n "$NS" wait --for=delete pod "$SRC" "$DST" --timeout=90s >/dev/null 2>&1 || true
"${K[@]}" -n "$NS" patch deploy "$DEPLOY" --type=merge \
  -p "{\"spec\":{\"template\":{\"spec\":{\"nodeSelector\":{\"kubernetes.io/hostname\":\"$NODE\"}}}}}" >/dev/null
"${K[@]}" -n "$NS" scale deploy "$DEPLOY" --replicas="$REPLICAS" >/dev/null
"${K[@]}" -n "$NS" rollout status deploy "$DEPLOY" --timeout=180s
echo "Done. Old data retained: PV $PV at $OLD_NODE:$OLD_PATH"
