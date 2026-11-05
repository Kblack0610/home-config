# Unity CI pool hosts

Operating the hosts of the org-wide Unity runner pool (label `unity`). Which jobs run where: unity-core-playground `docs/operations/ci.md`. Slot counts, cores and memory caps: [`ansible/inventory.yml`](../../ansible/inventory.yml) (`unity_ci_runners`, `unity_ci_mac_runners`, `ci_hosts`); how many slots may take work at once: [`apps/ci-runner-governor/config.json`](../../apps/ci-runner-governor/config.json).

## Apply the play

- From `ansible/`: `ansible-playbook playbooks/site.yml --limit <host> --tags unity-ci` (play "Unity CI runner pool", roles `unity-ci-host` and `github-actions-runner-linux`). The workstation's `gh` mints the org registration token and needs `admin:org` (`gh auth refresh -h github.com -s admin:org`).
- Each slot is its own runner under the `actions-runner` user in `/var/lib/actions-runner/unity-pool-<n>`, its HOME, so slots never share a Unity cache, a `Library/` or `~/.ssh`. They do share one asset-library LFS store, `/var/lib/actions-runner/.cache/bnb/asset-library-lfs` (`ASSET_LIBRARY_LFS_STORE`), so a new slot does not download the whole library first.
- Lowering `unity_ci_slots` does not remove a slot; the steps are in the play's header comment in `site.yml`.
- A new host goes into three rosters: the inventory, `RUNNER_ROSTER` in `apps/github-runner-exporter/deployment.yaml`, and the governor's `hosts` (`scripts/check-runner-roster.py` fails CI on drift).

## Unity activation

- A host is one activation whatever its slot count: every slot mounts one machine id, `/var/lib/actions-runner/unity-machine-id`. Deleting it makes the host a new machine.
- Every host activates on the shared Unity Personal serial. No per-machine cap has shown up; the limit hit is many activations at once (about 15 in 90 minutes got "Found 0 entitlement groups"), so add hosts one at a time.
- The first job on a new host proves it. If it says "Unity activation failed", in order: return stale activations on the Unity ID portal and rerun, run the host for light/heavy CI only, or buy a paid seat or Build Server licenses. Licensing: [unity-ci-hardware.md](../unity-ci-hardware.md#unity-license).

## Keep the host's copies current

- `unity-ci-host` puts the asset-library-hd copy (`/var/lib/actions-runner/asset-library-hd`) and the editor images on each host, copied from the workstation. Re-run it with `--tags unity-ci-host` after asset-library-hd's packages change and on a Unity upgrade.
- Unity upgrade: add the new tags to `unity_ci_images` (`ansible/roles/unity-ci-host/defaults/main.yml`), `docker load` them on the workstation, and run the role on every host before the upgrade merges. The `windows-mono`, `mac-mono` and `android` tags player builds use belong there too, or the build dies pulling its image.
- By hand, when the role cannot run or the workstation's checkout holds an uncommitted change (the role packs all of `Packages/`; tar only the new package folders then), from the workstation:
  - razer-laptop: `tar -C ~/dev/bnb/games/engine/asset-library-hd -cf - Packages | ssh kblack0610@192.168.1.26 'sudo tar -C /var/lib/actions-runner/asset-library-hd -xf - && sudo chown -R actions-runner:actions-runner /var/lib/actions-runner/asset-library-hd'`
  - Then compare `ls Packages | wc -l` and `find Packages -type f | wc -l` on the host with the workstation's.

## Editor images over IPv4

`docker pull` of an editor image fails on the home network: the IPv6 path corrupts long downloads (`tls: bad record MAC`) and docker cannot resume a layer. Until the router is fixed, fetch over IPv4 on the workstation with `scripts/test/fetch-image.py`, which lives in the unity-core-playground repo (run it from that checkout), and load it over ssh:

```sh
scripts/test/fetch-image.py unityci/editor ubuntu-<version>-base-3 ~/.cache/unity-image-fetch/blobs ~/.cache/unity-image-fetch/base && tar -C ~/.cache/unity-image-fetch/base -cf - . | ssh kblack0610@192.168.1.26 'sudo docker load'
```

`<version>` is from the playground's `ProjectSettings/ProjectVersion.txt`; the other tags go the same way. Delete `~/.cache/unity-image-fetch` afterwards.

## Labels

- Every slot carries `unity`, plus `unity-full` while the host's `unity_ci_full` is true (the default). Full runs (nightly, release, Multi) ask for `unity-full`, merge gates for `unity`. GitHub has no queue priority, so to reserve a runner for gates, set `unity_ci_full: false` on one host.
- A runner keeps the labels it registered with. On a live one, `gh api -X POST orgs/BlackNBrownStudios/actions/runners/<id>/labels -f 'labels[]=unity-full'` adds a label and `gh api -X DELETE orgs/BlackNBrownStudios/actions/runners/<id>/labels/unity-full` removes it.
- The workstation (cachyos-x8664-main) has two slots with `unity_ci_full: false`, so it takes merge gates and never a full run. The governor's `config.json` says the same with `"full": false`, since it re-labels a runner on admit and cannot read the inventory; change both together. Each slot has three cores and 16G of its own. A third slot waits for a day of load and temperatures with two. No gate runs anything else here (unity-core-playground `docs/operations/ci.md` rule 2).

## The Mac pool

- The "Unity CI runner pool (macOS)" play (roles `unity-ci-host-mac` and `github-actions-runner-mac`, group `unity_ci_mac_runners`). Details: [mac-machines.md](../mac-machines.md#unity-ci-pool-mac-studio-until-it-is-sold).
- Slots register without GitHub's default labels, only `unity`, `unity-native`, `host-<name>` and `unity-full`: with `self-hosted` and `macOS` they would take the platform repo's iOS jobs and DodginBalls' `[self-hosted, unity]` game-ci jobs.
- Slots are LaunchAgents, and macOS Local Network privacy keeps Homebrew git and git-lfs off the LAN (a LaunchDaemon too), so the playground fetches through the public host, `git.kennethblack.me`.
- Retiring the Studio: steps in the macOS play's header comment in `site.yml`. Plan the capacity first; its slots are a large share of the pool.

## The AWS burst host (aws-burst-1)

- An on-demand instance (m7a.8xlarge, us-east-2a, 6 slots, about $1.85 an hour while running) from the platform repo's `infra/aws-ci-burst` Terraform module. The 400G data volume (`/var/lib/actions-runner`, `/var/lib/docker`, `/var/lib/containerd`, label `ci-data`) and the machine id survive a stop and start; a stopped instance costs only its disks.
- Admin access is SSH through SSM, no inbound port: `ssh -o ProxyCommand='aws ssm start-session --profile bnb --region us-east-2 --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p' ubuntu@<instance-id>`. The instance id is in `ansible/inventory.yml`.
- Off the LAN: no `lan` label (`unity_ci_lan: false`), and the libraries come through S3 (`unity_ci_library_s3`) instead of a tarball over ssh.
- Each slot's first job imports its `Library/` cold, about 50 minutes; later jobs are warm (a PlayMode batch ran 10 minutes, against about 12 warm on mac-studio).
- Watch jobs from the host. The GitHub jobs API shares one 5000/h limit with every session, and polling it drained that limit on 2026-11-03: `sudo journalctl -f -n 0 -u "actions.runner.*" | grep -E "Running job|completed with result"`.
- A change to a runner unit or its `.env` restarts the slot and cancels its job. Drain first: remove `unity` with the org endpoint in Labels above (the repo endpoint answers 404 for org runners), wait for the slot to go idle, apply, add the label back.

### What starts and stops it

apps/ci-runner-governor powers it (config.json `standby` -> `aws-burst-1` -> `power`). The governor's docstring is the full rule; in short:

- Start: at least `start_min_excess` (4) waiting jobs it can take past what the other hosts cover, held for `start_after_seconds` (300). A short burst never starts it. Its slots count as room only once their runners are online.
- Stop: no slot busy for `idle_off_seconds` (900), or `max_hours_day` (12) hours run this UTC day. It withholds one slot per cycle and stops the instance only once no slot is busy, so a stop never kills a job.
- The day's hours are kept in the governor's memory: they reset at UTC midnight. After a pod restart a running instance counts from its EC2 launch time, so earlier runs that day are forgotten.
- The Infra page's Rule column shows where the rule stands, from the governor's own numbers: running, "busy" or "idle 6 of 15 min, then stops"; stopped, "2 jobs past the home hosts, starts at 4" or "held 2 of 5 min, then starts"; the day cap or an override when one applies. The governor panel under it shows, per host, live or dry run, its verdict and action, and its admitted slots.
- It needs the `ci-governor` IAM user's key (`ec2:StartInstances`, `ec2:StopInstances`, `ec2:DescribeInstances` on this instance) as `aws-access-key-id` and `aws-secret-access-key` in `ci-runner-governor-secret.yaml`. Without them it logs `power hook off` and does nothing. `"live": true` acts for real while the rest of the governor is still dry run.

### Holding it on or off by hand

- The Infra page of agent-web has a Cloud runners card: "On 2h", "Off 6h" and "Clear", each a two-click confirm. It writes the ConfigMap below with kubectl, so the page holds no AWS credential.
- Underneath is ConfigMap `ci-runner-governor-override` in `monitoring`, one key per host, `{"action":"on"|"off","until":<epoch>}`. It is not in Flux; the governor mounts it optional and ignores an expired or unreadable key. "on" starts the host and keeps it past idle-off and the day cap; "off" drains, stops and keeps it stopped.
- By hand: `kubectl -n monitoring create configmap ci-runner-governor-override --from-literal=aws-burst-1="{\"action\":\"off\",\"until\":$(( $(date +%s) + 6*3600 ))}" --dry-run=client -o yaml | kubectl apply -f -`, and `kubectl -n monitoring delete configmap ci-runner-governor-override` hands every host back.
- Metrics: `ci_governor_power_state{host,state,reason}`, `ci_governor_power_hours_today`, `ci_governor_power_max_hours_day` and `ci_governor_power_override{action}` (the override's end, 0 with none). The rule's progress: `_power_idle_seconds` of `_power_idle_off_seconds`, `_power_want` of `_power_start_min_excess`, `_power_held_seconds` of `_power_start_after_seconds`, `_power_last_change{action}` (epoch) and `_power_usd_per_hour` (config.json). They exist only while the power hook is on. `ci_governor_live{host}` is 1 for a host acted on for real, 0 for dry run.

### Pinning a host's slots

- `machine-mode reserved|shared|open` on the workstation writes `{"slots":N}` under its key in the same ConfigMap: 0, 1 or 2 slots admitted. The mode's table is the command's data file, in the dotfiles repo.
- A pinned host moves one slot per cycle toward N, past its cooldown and in either direction, and these moves happen even with `DRY_RUN=1`. A busy slot finishes its job first. The pool floor and cap still hold, and a FULL host keeps its verdict.
- The pin has no end time. A host with no pin, or an unreadable one, is back on its verdict, which under a dry run re-admits nothing, so set the mode back rather than deleting the key.
- The log says `PINNED`, and `ci_governor_decision{verdict="PINNED"}` shows it.

### The month's budget

- AWS Budgets `ci-burst-monthly` ($200, the platform module's `account.tf`) mails the alert address at $100 and $150, actual and forecast.
- The Infra page shows the month: AWS's actual and forecast against the limit, the alert and action thresholds, and today's hours times `usd_per_hour`. The governor reads the budget once an hour with its own key (`budgets:ViewBudget` on this budget only, platform `iam.tf`) and exports `ci_governor_budget_usd{kind="actual|forecast|limit"}`, `_budget_alert{basis,usd}`, `_budget_action_usd{type,status}`, `_budget_updated` (AWS's last refresh, a few times a day) and `_budget_ok`. A failed read keeps the last numbers and never changes a power decision. AWS's forecast can sit below actual early in an account's life; the page flags it.
- At $180 actual it runs two actions on its own: an SSM stop of this instance, and attaching IAM policy `ci-burst-deny-start` (deny `ec2:StartInstances`) to `ci-governor`, so the governor cannot start it again. The governor then logs failed starts; that is expected.
- Budget actions do not undo themselves at the new month. To reverse one (Budget stop): detach the policy, `aws iam detach-user-policy --user-name ci-governor --policy-arn arn:aws:iam::311141527647:policy/ci-burst-deny-start --profile bnb`; find the action ids with `aws budgets describe-budget-actions-for-budget --account-id 311141527647 --budget-name ci-burst-monthly --profile bnb`; for each, `aws budgets execute-budget-action --account-id 311141527647 --budget-name ci-burst-monthly --action-id <id> --execution-type REVERSE_BUDGET_ACTION --profile bnb`. The governor starts the instance at the next deep queue, or start it by hand.

### Sentinel

- Watch `aws-burst-power` (`~/.agent/watches/aws-burst-power.yaml`) reads the governor's power metrics every 5 minutes and notifies when the host goes from stopped to running and back, with the reason and the hours run today. No data reads as stopped, so it stays quiet while the power hook is off.

| Symptom | Cause | Fix |
|---|---|---|
| Play or job fails with "no space left on device" | Docker 29 keeps images in `/var/lib/containerd`, on the 38G root disk | The Terraform user data binds it to the data volume (platform #1459) |
| Library pull from S3 runs at under 1 MB/s | `aws s3 sync` sends 10 requests at a time over about 170k small files | 64 at a time, both on the workstation push and the host pull (home-config #505) |
| Every job fails in seconds on `index.lock`: File exists, after a stop | The stop interrupted a checkout and left git locks in every slot | The runner unit deletes `.git/**/*.lock` under the work dir before it starts (home-config #510) |
| A slot's `docker run` finds no test image right after its own build | Ubuntu's `docker.io` has no BuildKit, and four slots building one tag at once raced on the legacy builder | `docker-buildx` in `unity_ci_packages_debian` (home-config #511) |
| A push over 100 MB to git.kblab.me fails | Cloudflare drops bodies over 100 MB, and the host reaches Forgejo only through it | Jobs that push big files ask for `lan`, which this host does not carry |
| The first test of a PlayMode batch times out at `SwapTo` ("scene loaded False, host sees it False"), and its `[Load] sandbox3d` line shows `Composing` of 10-20 s, against about 1 s on mac-studio | A fresh Unity process does its first-scene work on a slot's own cores; at 4 cores per slot every slot ran about 3.5x oversubscribed | `ci_unity_slot_cpus` spreads the cores over the slots; a slice change is a `daemon-reload`, so running jobs keep going |
| The governor log says `power hook off for aws-burst-1` | No `aws-access-key-id` in its secret | Put the `ci-governor` key in with sops (above) |
| The box sits stopped while jobs wait | One of: fewer than 4 jobs it can take past the other hosts, a queue under 5 minutes old, its 12h day used, an "off" override, the budget's deny policy | Read the governor log's `aws-burst-1` line for which; "On 2h" on the Infra page overrides all but the budget |
| The Infra page's month line is stale or missing, and `ci_governor_budget_ok` is 0 | The governor log says `budget ci-burst-monthly unread`: an AccessDenied means the key lacks `budgets:ViewBudget` (platform `iam.tf` statement `ReadBurstBudget`), a timeout means no route to budgets.amazonaws.com | Apply the platform module; the next read is within an hour, or restart the governor pod to read at once |
| Starts fail with `UnauthorizedOperation` | The $180 budget action attached `ci-burst-deny-start` | Budget stop above |
| It ran past 12h | An "on" override, or the governor pod restarted after an earlier run that day (the count restarts from the current run's launch time) | Expected for the override; the AWS budget is the cap that cannot forget |
| Offline runners `aws-burst-1-unity-pool-7`, `-8` with no `unity` label | #525 cut `unity_ci_slots` from 8 to 6 and left those two registered; a lowered count removes nothing (top of this page) | Deregistered 2026-11-04. Next time, run `scripts/unity-ci-runner-remove.sh --host <host> --slot N` for each dropped slot |

## Secrets the playground workflows need

- `UNITY_SERIAL`, repo secret on unity-core-playground: DodginBalls' Personal serial (reading it from a `Unity_lic.ulf`: dodginballs `docs/operations/ci-findings.md`).
- Org secrets `UNITY_EMAIL`, `UNITY_PASSWORD` and `UNITY_CORE_DEPLOY_KEY` (the SSH deploy key for the unity-core submodule). The password is on Unity's command line inside the container, so `ps` on the host shows it.
- `ASSET_LIBRARY_TOKEN`: a Forgejo token with `read:repository` for the asset-library submodule on `git.kblab.me`.
- Org runners run whatever any org repo's pull request workflow says. Keep the repos private, or require approval for outside contributors' runs.

## Not Unity hosts: asus-laptop and hp-victus

Both are k3s nodes and, since 2026-11-03, light/heavy platform runners only. Unity load there broke the cluster along with the jobs: asus holds forgejo and the databases and ran at 90C, and hp-victus (31G) had 279 OOM kills in its Unity slices from 10-29 to 11-03, some of them litellm and prometheus when the whole host ran out. Their slots were removed with `scripts/unity-ci-runner-remove.sh`; the reasons are in `ansible/inventory.yml` above `unity_ci_runners`. Do not add either back.
