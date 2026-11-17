# Recovering lost Actions assignments

This runbook diagnoses tasks lost during fetch and activates the upstream recovery fix.

Forgejo 14.0.4 and runner 6.3.1 can lose an assignment when the server commits it but the runner never receives the FetchTask response. The runner then fetches another job while the first task remains running in the database. Forgejo's zombie cleanup eventually fails that first task, making a PR gate red without executing its job.

## Diagnosis

To diagnose an empty-log failure, join `action_task.runner_id` to `action_runner.id` and compare the task ID with that runner's logs. Assignment without a corresponding runner log can indicate a lost FetchTask response. In [Forgejo 14.0.4 PickTask](https://codeberg.org/forgejo/forgejo/src/tag/v14.0.4/services/actions/task.go), synchronous commit-status work follows the assignment transaction; a context deadline there can strand a committed task.

The runner's [default fetch deadline is 5s](https://code.forgejo.org/forgejo/runner/src/tag/v6.3.1/internal/pkg/config/config.go). Its [poller](https://code.forgejo.org/forgejo/runner/src/tag/v6.3.1/internal/app/poll/poller.go) logs deadline expiry only at Trace. In Forgejo's [router logger](https://codeberg.org/forgejo/forgejo/src/tag/v14.0.4/modules/web/routing/logger.go), `router: slow ... elapsed 3711ms` is a StillExecutingEvent from the slow-query detector. It measures an unfinished request; it is not the final response time.

Forgejo now runs on forge-hel1 through Ansible; home-k3s retains the runner and an inactive rollback Deployment. The live database is already in WAL mode. Changing its journal mode or increasing the zombie timeout would not repair assignment recovery.

## Repair

[Forgejo #11401](https://codeberg.org/forgejo/forgejo/pulls/11401) and [runner #1393](https://code.forgejo.org/forgejo/runner/pulls/1393) implement an idempotent FetchTask request key. The runner keeps the same key across failed fetches, allowing the server to return the existing assignment. [Forgejo #13658](https://codeberg.org/forgejo/forgejo/pulls/13658), backported in [#13673](https://codeberg.org/forgejo/forgejo/pulls/13673), also locks that key while the original request is still running.

The complete fix requires Forgejo 15.0.6 or later and runner 12.7.1 or later. The manifests pin Forgejo 15.0.11 LTS and runner 13.2.1 by registry digest. This replaces the old binaries; no local timeout override, retry wrapper or custom server patch is needed. The newer runner's default fetch deadline is 30s, but assignment recovery is what protects against a lost reply.

Runner 13 retains the configured YAML keys, the registration flags, `.runner` loading and the task log format consumed by github-runner-exporter. Docker 27 meets its Docker 25 minimum. Its stricter workflow validation and removal of legacy workflow commands require checking the instance's workflows before activation; see the [runner 13 release notes](https://forgejo.org/2026-08-runner-release-v13/). The [Forgejo 15 release notes](https://forgejo.org/2026-04-release-v15-0/) also describe API token permission changes and a login cookie name change. Expect users to sign in again.

## Activation and verification

This change needs approval for the database migration before merge and activation. Upgrading the primary changes its schema; reverting the image alone is not a rollback. Follow the [upstream upgrade guide](https://forgejo.org/docs/v15.0/admin/upgrade/) and the [host runbook](../apps/forgejo/README.md#where-forgejo-runs-and-how-to-move-it).

1. Confirm `forge-primary.kblab.me` still names forge-hel1 and that only that host runs Forgejo. Check a current full offbox backup and a fresh consistent SQLite snapshot; retain the old image and pre-upgrade data together. Do not restore against a running writer.
2. Arrange a maintenance window and let picked-up jobs finish before replacing the runner or restarting Forgejo. Check the runner logs and live task state; a runner restart can itself abandon active jobs. Avoid admitting new work during this window.
3. Merge through `agent-land`. Flux updates the runner; the server stays on its old version until the Ansible play runs. A runner upgrade by itself does not repair the old server.
4. Re-fetch master, review `ansible-playbook playbooks/site.yml --limit forge-hel1 --tags forgejo --skip-tags monitoring --check --diff` from `ansible/`, then run it without `--check --diff`. Confirm the reviewed changes are limited to this upgrade. The role renders the same app.ini and restarts Forgejo through its handler.
5. Verify `/api/v1/version` reports 15.0.11, the runner reports 13.2.1, and the runner registration and task logs work. Verify a new PR's gates execute and complete. Check the mirror exporter and registry access after the schema upgrade.
6. On an isolated test instance, interrupt a FetchTask response after assignment and retry with the same `x-runner-request-key`. Confirm it returns the original task, including when the retry overlaps the first request. Do not inject response loss into production gates. Upstream's recovery tests cover this protocol; local manifest and migration smoke checks do not prove that production fault injection has run.

| Symptom | Check | Action |
|---------|-------|--------|
| Jobs fail together after roughly 10 minutes with no step logs | Join `action_task.runner_id` to `action_runner.id`; compare task IDs with that runner's logs | Confirm both server and runner include request-key recovery |
| Slow FetchTask logs appear below the fetch deadline | Inspect the router logger's StillExecutingEvent and nearby context errors | Treat the message as an in-progress sample; do not infer a completed response |
| Runner is upgraded but empty-log zombie failures continue | Read the version from the live server | Apply the approved host upgrade through Ansible |
| New runner rejects a workflow | Read the validation error and runner release notes | Correct the workflow syntax or legacy commands before reopening its gate |
| Mirror exporter reports `too_large` after the server upgrade | Run its direct database query; Forgejo 15 replaces `mirror.remote_address` with encrypted data | Read the public upstream URL from `repository.original_url`; the exporter needs no decryption key or larger snapshot limit |
