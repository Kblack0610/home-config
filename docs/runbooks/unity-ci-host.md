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

- Every slot carries `unity`, plus `unity-full` while the host's `unity_ci_full` is true (the default). Full runs (nightly, release, Multi) ask for `unity-full`, merge gates for `unity` and `unity-gate`. GitHub has no queue priority, so to reserve a runner for gates, set `unity_ci_full: false` on one host.
- `unity-gate` (`unity_ci_gate`, default false) is on the hosts a merge gate may land on: razer-laptop, cachyos-x8664-main and aws-burst-1. aws-burst-2 is Spot and leaves it off, so a reclaim never kills a gate (playground ucp-1778). The governor's withhold swap leaves it alone: a gate also asks for `unity`, which a withheld slot has lost.
- A runner keeps the labels it registered with, `unity_ci_slot_labels` too: on a registered slot add them with the POST below (aws-burst-1-unity-pool-1 got `host-aws-burst-1-wide` that way on 11-10). On a live one, `gh api -X POST orgs/BlackNBrownStudios/actions/runners/<id>/labels -f 'labels[]=unity-full'` adds a label and `gh api -X DELETE orgs/BlackNBrownStudios/actions/runners/<id>/labels/unity-full` removes it.
- The workstation (cachyos-x8664-main) has two slots with `unity_ci_full: false`, so it takes merge gates and never a full run. The governor's `config.json` says the same with `"full": false`, since it re-labels a runner on admit and cannot read the inventory; change both together. Each slot has three cores and 16G of its own. A third slot waits for a day of load and temperatures with two. No gate runs anything else here (unity-core-playground `docs/operations/ci.md` rule 2).

## The Mac pool

- The "Unity CI runner pool (macOS)" play (roles `unity-ci-host-mac` and `github-actions-runner-mac`, group `unity_ci_mac_runners`). Details: [mac-machines.md](../mac-machines.md#unity-ci-pool-on-a-mac-none-since-2026-11-10).
- Slots register without GitHub's default labels, only `unity`, `unity-native`, `host-<name>` and `unity-full`: with `self-hosted` and `macOS` they would take the platform repo's iOS jobs and DodginBalls' `[self-hosted, unity]` game-ci jobs.
- Slots are LaunchAgents, and macOS Local Network privacy keeps Homebrew git and git-lfs off the LAN (a LaunchDaemon too), so the playground fetches through the public host, `git.kennethblack.me`.
- No Mac is in the pool since 2026-11-10: the Mac Studio was sold and its six slots moved to aws-burst-2. The play and roles stay for a future Mac; retiring one is the macOS play's header comment in `site.yml`.

## The AWS burst host (aws-burst-1)

- An on-demand instance (m7a.8xlarge, us-east-2a, 5 slots, slot 1 ten cores wide with `host-aws-burst-1-wide` and `unity-wide` (one wide job at a time), every slot `hd-library`, about $1.85 an hour while running) from the platform repo's `infra/aws-ci-burst` Terraform module. The 400G data volume (`/var/lib/actions-runner`, `/var/lib/docker`, `/var/lib/containerd`, label `ci-data`) and the machine id survive a stop and start; a stopped instance costs only its disks.
- Admin access is SSH through SSM, no inbound port: `ssh -o ProxyCommand='aws ssm start-session --profile bnb --region us-east-2 --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p' ubuntu@<instance-id>`. The instance id is in `ansible/inventory.yml`.
- Labels name a need (unity-core-playground `docs/operations/ci.md#the-runner-pool`): `unity-wide` (`unity_ci_wide`, default false) marks a slot with the CPU for Multi, Soak and Playloop, and only slots that passed the ucp-1521 probes carry it (razer-laptop's by the host flag, aws-burst-1's slot 1 by `unity_ci_slot_labels`, since its other slots are too narrow), and a wide slot needs 20G of memory, which `scripts/check-runner-roster.py` checks, so aws-burst-2's 16G slots stay plain (playground ucp-1830; to widen them, raise `ci_unity_slot_memory_max` to 20G first, then the flag and the live label); `hd-library` (`unity_ci_hd_library`, default `unity_ci_lan`) marks a host holding asset-library-hd. A new label on a live runner: the org labels endpoint in Labels above, then the inventory flag, so an apply keeps it. Add a label here before a playground workflow asks for it.
- Off the LAN: no `lan` label (`unity_ci_lan: false`), and the libraries come through S3 (`unity_ci_library_s3`) instead of a tarball over ssh. So does the shared asset-library LFS store (`tasks/lfs-store.yml`, prefix `asset-library-lfs/objects`), seeded from the workstation's `~/.cache/bnb/asset-library-lfs`: a cold release tree fetching about 4 GB of LFS through the Cloudflare tunnel ended in `batch response: Authorization error` (aws-build-1, run 42905629793). Re-run `--tags unity-ci` after the asset-library pin moves and the workstation has pulled it, or a release fetches the new objects through the tunnel.
- Each slot's first job imports its `Library/` cold, about 50 minutes; later jobs are warm (a PlayMode batch ran 10 minutes, against about 12 warm on mac-studio).
- Watch jobs from the host. The GitHub jobs API shares one 5000/h limit with every session, and polling it drained that limit on 2026-11-03: `sudo journalctl -f -n 0 -u "actions.runner.*" | grep -E "Running job|completed with result"`.
- A change to a runner unit or its `.env` restarts the slot and cancels its job. Drain first: remove `unity` with the org endpoint in Labels above (the repo endpoint answers 404 for org runners), wait for the slot to go idle, apply, add the label back.

### What starts and stops it

apps/ci-runner-governor powers it (config.json `standby` -> `aws-burst-1` -> `power`). The governor's docstring is the full rule; in short:

- Order: `standby` is a list, and the governor starts its hosts in that order (Spot first once `aws-burst-2` is in it, then the on-demand `aws-burst-1`). A host further down starts only for the jobs the hosts ahead cannot take, so a short queue starts one host and a deep one starts both.
- Fallback: a stopped host whose start EC2 refuses (`InsufficientInstanceCapacity` is `capacity`; any other EC2 code, such as a Spot price or request state or a vCPU limit, is `refused`), or that AWS stops (a Spot reclaim, or a start that found no capacity after all: any `Server.*` state reason, `reclaim`), is passed over for `fallback_seconds` (3600), and the next host takes the queue at once. AWS restarting a persistent Spot host by itself ends the pass-over. `ci_governor_fallback_total{host,reason="capacity|refused|reclaim"}` counts each pass-over once, `ci_governor_power_fallback_until{host}` is its end, and a reclaim shows as `reason="reclaim"` on `ci_governor_power_state`. Not a fallback: the budget deny policy (`UnauthorizedOperation`), which covers every host, and a network error.

- Start: at least `start_min_excess` (4) waiting jobs it can take past what the other hosts cover, held for `start_after_seconds` (300). A short burst never starts it. Its slots count as room only once their runners are online.
- Stop: no slot busy for `idle_off_seconds` (900), or `max_hours_day` (12) hours run this UTC day. It withholds one slot per cycle and stops the instance only once no slot is busy, so a stop never kills a job.
- The day's hours reset at UTC midnight. After a pod restart the governor reads back the highest `ci_governor_power_hours_today` it exported that day and the newest reason other than `none` (a running instance: since its EC2 launch; a stopped one: today), so a deploy resets neither the cap nor the reason (a host first seen already running, started outside the governor, keeps `none` until its next start); with Prometheus unreachable it counts from the EC2 launch time and logs `earlier run time and cause unread`.
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
| aws-burst-1 runners die mid-run ("lost communication", bee "Request cancelled while reading the binlog", then ENOSPC) | `/var/lib/ci` full: `Builds/release-tree` and `Builds/hd-tree` copies (26G each) and windows, mac and android editor images left from before player builds moved to `lan` hosts | `df -h /var/lib/ci`; the hourly `linux-ci-docker-cleanup` prune keeps it down, and the host pulls base images only (`unity_ci_images` in inventory.yml). Remove unlisted `unityci/editor` images by hand: the prune never removes them |
| Library pull from S3 runs at under 1 MB/s | `aws s3 sync` sends 10 requests at a time over about 170k small files | 64 at a time, both on the workstation push and the host pull (home-config #505) |
| Every job fails in seconds on `index.lock`: File exists, after a stop | The stop interrupted a checkout and left git locks in every slot | The runner unit deletes `.git/**/*.lock` under the work dir before it starts (home-config #510) |
| A slot's `docker run` finds no test image right after its own build | Ubuntu's `docker.io` has no BuildKit, and four slots building one tag at once raced on the legacy builder | `docker-buildx` in `unity_ci_packages_debian` (home-config #511) |
| A push over 100 MB to git.kblab.me fails | Cloudflare drops bodies over 100 MB, and the host reaches Forgejo only through it | Jobs that push big files ask for `lan`, which this host does not carry |
| The first test of a PlayMode batch times out at `SwapTo` ("scene loaded False, host sees it False"), and its `[Load] sandbox3d` line shows `Composing` of 10-20 s, against about 1 s on mac-studio | A fresh Unity process does its first-scene work on a slot's own cores; at 4 cores per slot every slot ran about 3.5x oversubscribed | `ci_unity_slot_cpus` spreads the cores over the slots; a slice change is a `daemon-reload`, so running jobs keep going |
| The governor log says `power hook off for aws-burst-1` | No `aws-access-key-id` in its secret | Put the `ci-governor` key in with sops (above) |
| The box sits stopped while jobs wait | One of: fewer than 4 jobs it can take past the other hosts, a queue under 5 minutes old, its 12h day used, an "off" override, the budget's deny policy | Read the governor log's `aws-burst-1` line for which; "On 2h" on the Infra page overrides all but the budget |
| The Infra page's month line is stale or missing, and `ci_governor_budget_ok` is 0 | The governor log says `budget ci-burst-monthly unread`: an AccessDenied means the key lacks `budgets:ViewBudget` (platform `iam.tf` statement `ReadBurstBudget`), a timeout means no route to budgets.amazonaws.com | Apply the platform module; the next read is within an hour, or restart the governor pod to read at once |
| Starts fail with `UnauthorizedOperation` | The $180 budget action attached `ci-burst-deny-start` | Budget stop above |
| It ran past 12h | An "on" override, or a pod restart that could not read Prometheus (log: `earlier run time and cause unread`) | Expected for the override; the AWS budget is the cap that cannot forget |
| Offline runners `aws-burst-1-unity-pool-7`, `-8` with no `unity` label | #525 cut `unity_ci_slots` from 8 to 6 and left those two registered; a lowered count removes nothing (top of this page) | Deregistered 2026-11-04. Next time, run `scripts/unity-ci-runner-remove.sh --host <host> --slot N` for each dropped slot |

## The AWS build host (aws-build-1)

- The player-build lane: an on-demand m7a.8xlarge (us-east-2, 3 slots of 10 cores and 36G, 600G data volume) from the platform `infra/aws-ci-burst` `hosts` map. Inventory `unity_ci_pool: false` and `unity_ci_build: true` give its runners `unity-build` and no `unity`, `unity-full` or `lan`, so no gate or nightly lands there and a build never waits on one. release.yml asks for `[self-hosted, linux, unity-build]`; its `host` input pins a build to another host instead (the razer release lane).
- The governor powers it as a dedicated standby host (config.json `standby` -> `aws-build-1`, with `labels` and `withheld_label`): one waiting build starts it after 60 s, and it stops 15 minutes after its last busy slot. A withheld slot carries `unity-build-withheld`. The exporter counts it as pool `unity-build`, so its waits stay out of the gate pool's numbers.
- Setup and activation are the same as aws-burst-1 above (`--limit aws-build-1`). The host pulls the whole default image list, since a desktop build needs the windows and mac editor images.
- Another build host: one `hosts` entry in Terraform, one inventory entry modeled on this one, one standby entry, and the rosters. `scripts/check-runner-roster.py` checks the last three agree.

## Secrets the playground workflows need

- `UNITY_SERIAL`, repo secret on unity-core-playground: DodginBalls' Personal serial (reading it from a `Unity_lic.ulf`: dodginballs `docs/operations/ci-findings.md`).
- Org secrets `UNITY_EMAIL`, `UNITY_PASSWORD` and `UNITY_CORE_DEPLOY_KEY` (the SSH deploy key for the unity-core submodule). The password is on Unity's command line inside the container, so `ps` on the host shows it.
- `ASSET_LIBRARY_TOKEN`: a Forgejo token with `read:repository` for the asset-library submodule on `git.kblab.me`.
- Org runners run whatever any org repo's pull request workflow says. Keep the repos private, or require approval for outside contributors' runs.

## Disk

The hourly `linux-ci-docker-cleanup.timer` runs `/usr/local/lib/linux-ci/ci-disk-prune.sh` (roles/linux-ci-host, `tasks/disk.yml`) on every Linux CI host, Unity-only ones too. On idle slots it removes release trees and HD trees (`Builds/hd-tree`, 26G, kept by hd.yml) not touched in 7 days and every copy of one but the newest; then, while the disk is under 35% free, idle slots' release and HD trees oldest first. Every Unity slot also runs it as its job-started hook (`unity-job-started.sh`, `--before-job`): under 35% free it prunes idle slots' trees before the job writes, and above it does nothing. That hook is what holds the AWS hosts back: the governor withholds a host's Unity slots under 20% free and re-admits them at 25% (`ci_governor_disk_held`), but its disk reading is node_exporter's `/`, and Prometheus has none for aws-burst-1, aws-burst-2 or aws-build-1, whose slots write to `/var/lib/ci`. A lock on the runner home keeps the hook and the timer from pruning at once. Changing a slot's hook env restarts the runner and kills its job, so apply it with the host's slots idle. Check it with `systemctl list-timers linux-ci-docker-cleanup.timer` and `journalctl -u linux-ci-docker-cleanup.service`. On razer-laptop the forgejo-runner pod's dind store is also on `/`: it has a 60Gi limit and a prune sidecar (apps/forgejo/README.md, Runner).

A change to `ci-disk-prune.sh` or `unity-job-started.sh` reaches a host only when the play runs there: the convergence-check CronJob runs `--check`, so nothing applies it to any host. After such a merge, from `ansible/`: `ansible-playbook playbooks/site.yml --limit unity_ci_runners:linux_ci_runners:mac-mini --tags ci-disk` (without the limit the OpenWrt play's `always` tasks run too). It rewrites the scripts and the timer and restarts no slot, so it is safe with jobs running. A stopped AWS host fails as UNREACHABLE and the run exits 4: apply that host after its next start. To check a Linux host, compare `sha1sum /usr/local/lib/linux-ci/ci-disk-prune.sh` there with `git show forgejo/master:ansible/roles/linux-ci-host/files/ci-disk-prune.sh | sha1sum` (mac-mini runs `ci-tree-prune.sh` from roles/unity-ci-host-mac instead). A host that never had the hook also needs its slots' `ACTIONS_RUNNER_HOOK_JOB_STARTED` env, which only `--tags unity-ci` sets and which restarts the slots: apply it with them idle.

## GitHub API limit

The exporter's token has two 5000-an-hour buckets: `orgs/` calls (runners) and `repos/` calls (runs, jobs). When one hits 0 the exporter backs off until the hour resets, and the governor, which reads the exporter, stops moving Unity slots. `GithubApiRateLow` warns when a bucket stays under 500 for 10 minutes; `CiGovernorBlind` pages once the governor has been blind for 30. To find what spent it, compare `sum by (kind) (increase(github_runner_exporter_api_calls_total[1h]))` with `github_runner_exporter_api_not_modified_total` (free 304s): a cold cache after an exporter restart, or a new repo with a long run history, is the usual spender (`HISTORY_DEADLINE_SECS` in `apps/github-runner-exporter/exporter.py`). Another tool sharing the same token spends it too, and the exporter's counters do not show it.

## Not Unity hosts: asus-laptop and hp-victus

Both are k3s nodes and, since 2026-11-03, light/heavy platform runners only. Unity load there broke the cluster along with the jobs: asus holds forgejo and the databases and ran at 90C, and hp-victus (31G) had 279 OOM kills in its Unity slices from 10-29 to 11-03, some of them litellm and prometheus when the whole host ran out. Their slots were removed with `scripts/unity-ci-runner-remove.sh`; the reasons are in `ansible/inventory.yml` above `unity_ci_runners`. Do not add either back.
