# MinIO (S3-compatible object storage)

Single-node MinIO used as a remote backend for Terraform state across infra
modules in `bnb/platform/infra/`. Bucket convention: `terraform-state`, key
pattern `<module-name>/terraform.tfstate`.

## Endpoints

- **S3 API** (for Terraform / clients): https://s3.kblab.me
- **Web console**: https://minio.kblab.me

Both LAN/Tailscale-only via internal DNS (`kblab.me` resolves to home-k3s
ingress at 192.168.1.100). Not exposed publicly via the Cloudflare tunnel.

## Credentials

Root credentials live in `secret.yaml` (SOPS-encrypted with the cluster's
age key, see `~/dev/home/home-config/.sops.yaml`). For Terraform, set:

```bash
export AWS_ACCESS_KEY_ID=<MINIO_ROOT_USER>
export AWS_SECRET_ACCESS_KEY=<MINIO_ROOT_PASSWORD>
```

Or decrypt the secret: `sops -d apps/minio/secret.yaml`.

## Terraform backend pattern

```hcl
terraform {
  backend "s3" {
    endpoints = {
      s3 = "https://s3.kblab.me"
    }
    region                      = "us-east-1"  # ignored by MinIO
    bucket                      = "terraform-state"
    key                         = "<module-name>/terraform.tfstate"
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
    use_path_style              = true
  }
}
```

## Storage

10Gi PVC on the default `local-path` storage class. State files are tiny so
this is generous. PVC is on whichever node MinIO ends up on — if that node
is lost, state is lost. Consider periodic backups via the `nas` Samba export.
