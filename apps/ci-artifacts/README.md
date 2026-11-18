# CI artifacts

The S3 store CI artifacts go to instead of GitHub (plan gh-artifacts-nas, phase 2). A single MinIO on asus-laptop whose data is `/mnt/nas/private/ci-artifacts` on the NAS disk, so artifact churn stays off the root disk that every local-path PV and the `minio` app share. Nothing backs it up: an artifact is rebuilt by re-running its job. What goes in it, and how long each class lives: unity-core-playground `docs/operations/ci-artifacts.md`.

## Endpoints

S3 API on NodePort 30900, plain HTTP, no ingress:

| Who | Endpoint |
|---|---|
| LAN pool hosts | `http://192.168.1.152:30900` |
| AWS hosts (`unity_ci_lan: false`, on the tailnet through `roles/tailscale-node`) | `http://100.83.14.33:30900` (asus-laptop's tailnet address) |
| Pods | `http://ci-artifacts.ci-artifacts.svc:9000` |

Tailnet traffic is WireGuard-encrypted. LAN traffic is plain HTTP, the same trust as the runners' other LAN services. Use path-style addressing (`--endpoint-url` with the AWS CLI, `mc alias set`).

## Bucket and retention

One bucket, `ci-artifacts`. The first key segment is the retention: `1d/`, `3d/`, `7d/`, `14d/`, `30d/`, `90d/`. MinIO's lifecycle scanner deletes an object that many days after it was written, so a writer picks the prefix for its class, and a reader that asks "is it still live" adds the prefix's days to the object's LastModified. A key outside those prefixes never expires, so writers must not use one. The rules, a 500 GiB quota and the CI user come from `bucket-job.yaml`; the job's import replaces the whole rule set, so that file is the one list. To change it, edit the job and bump its name.

## Credentials

`secret.sops.yaml` holds the root user and the CI user (`CI_ACCESS_KEY`, `CI_SECRET_KEY`), which may read, write and delete in `ci-artifacts` only. Workflows get the CI user; nothing outside this namespace gets root.

```bash
sops -d apps/ci-artifacts/secret.sops.yaml
```

## Verify

```bash
kubectl --context home-k3s get pods -n ci-artifacts -o wide
kubectl --context home-k3s logs -n ci-artifacts job/ci-artifacts-bucket-2
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.1.152:30900/minio/health/live   # 200
```

Gatus checks it as "CI artifacts (S3)" in the home-k3s group.
