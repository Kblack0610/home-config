# home-config Project Rules

Inherits shared rules from `~/CLAUDE.md`. The following are specific to this repository.

## Deployment Model

- **All `apps/` changes deploy through Flux.** Never run `kubectl apply` on Flux-managed resources. The workflow is: edit manifests → commit → push → Flux reconciles (or `flux reconcile kustomization apps --with-source`).
- Direct `kubectl apply` is only valid for initial Flux bootstrap, resources outside Flux's watch path (`infrastructure/`), or emergency recovery with Flux suspended.
- See `docs/gitops.md` for the full workflow.
