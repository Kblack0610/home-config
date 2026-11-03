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
- The workstation (cachyos-x8664-main) has two slots with `unity_ci_full: false`, so it takes merge gates and never a full run. Each slot has three cores and 16G of its own. A third slot waits for a day of load and temperatures with two. No gate runs anything else here (unity-core-playground `docs/operations/ci.md` rule 2).

## The Mac pool

- The "Unity CI runner pool (macOS)" play (roles `unity-ci-host-mac` and `github-actions-runner-mac`, group `unity_ci_mac_runners`). Details: [mac-machines.md](../mac-machines.md#unity-ci-pool-mac-studio-until-it-is-sold).
- Slots register without GitHub's default labels, only `unity`, `unity-native`, `host-<name>` and `unity-full`: with `self-hosted` and `macOS` they would take the platform repo's iOS jobs and DodginBalls' `[self-hosted, unity]` game-ci jobs.
- Slots are LaunchAgents, and macOS Local Network privacy keeps Homebrew git and git-lfs off the LAN (a LaunchDaemon too), so the playground fetches through the public host, `git.kennethblack.me`.
- Retiring the Studio: steps in the macOS play's header comment in `site.yml`. Plan the capacity first; its slots are a large share of the pool.

## The AWS burst host (aws-burst-1)

- A persistent Spot instance (m7a.12xlarge, us-east-2a, 8 slots of 4 cores and 14G) from the platform repo's `infra/aws-ci-burst` Terraform module. A Spot interruption stops it; the data volume (`/var/lib/actions-runner`, `/var/lib/docker`, `/var/lib/containerd`) and the machine id survive a stop and start.
- apps/ci-runner-governor powers it (config.json `standby` `power`): it starts the instance when more pool jobs wait than the other hosts can take, admits its slots once their runners are online, and after `idle_off_seconds` (20 minutes) with no slot busy withholds them and stops it. It needs an IAM key allowed `ec2:StartInstances`, `ec2:StopInstances` and `ec2:DescribeInstances` on this instance, as `aws-access-key-id` and `aws-secret-access-key` in `ci-runner-governor-secret.yaml`; without them it logs `power hook off` and does nothing. `"live": true` acts for real while the rest of the governor is still dry run.
- Admin access is SSH through SSM, no inbound port: `ssh -o ProxyCommand='aws ssm start-session --profile bnb --region us-east-2 --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p' ubuntu@<instance-id>`. The instance id is in `ansible/inventory.yml`.
- Off the LAN: no `lan` label (`unity_ci_lan: false`), and the libraries come through S3 (`unity_ci_library_s3`) instead of a tarball over ssh.
- Each slot's first job imports its `Library/` cold, about 50 minutes; later jobs are warm (a PlayMode batch ran 10 minutes, against about 12 warm on mac-studio).
- Watch jobs from the host. The GitHub jobs API shares one 5000/h limit with every session, and polling it drained that limit on 2026-11-03: `sudo journalctl -f -n 0 -u "actions.runner.*" | grep -E "Running job|completed with result"`.
- A change to a runner unit or its `.env` restarts the slot and cancels its job. Drain first: remove `unity` with the org endpoint in Labels above (the repo endpoint answers 404 for org runners), wait for the slot to go idle, apply, add the label back.

| Symptom | Cause | Fix |
|---|---|---|
| Play or job fails with "no space left on device" | Docker 29 keeps images in `/var/lib/containerd`, on the 38G root disk | The Terraform user data binds it to the data volume (platform #1459) |
| Library pull from S3 runs at under 1 MB/s | `aws s3 sync` sends 10 requests at a time over about 170k small files | 64 at a time, both on the workstation push and the host pull (home-config #505) |
| Every job fails in seconds on `index.lock`: File exists, after a Spot stop | The stop interrupted a checkout and left git locks in every slot | The runner unit deletes `.git/**/*.lock` under the work dir before it starts (home-config #510) |
| A slot's `docker run` finds no test image right after its own build | Ubuntu's `docker.io` has no BuildKit, and four slots building one tag at once raced on the legacy builder | `docker-buildx` in `unity_ci_packages_debian` (home-config #511) |
| A push over 100 MB to git.kblab.me fails | Cloudflare drops bodies over 100 MB, and the host reaches Forgejo only through it | Jobs that push big files ask for `lan`, which this host does not carry |

## Secrets the playground workflows need

- `UNITY_SERIAL`, repo secret on unity-core-playground: DodginBalls' Personal serial (reading it from a `Unity_lic.ulf`: dodginballs `docs/operations/ci-findings.md`).
- Org secrets `UNITY_EMAIL`, `UNITY_PASSWORD` and `UNITY_CORE_DEPLOY_KEY` (the SSH deploy key for the unity-core submodule). The password is on Unity's command line inside the container, so `ps` on the host shows it.
- `ASSET_LIBRARY_TOKEN`: a Forgejo token with `read:repository` for the asset-library submodule on `git.kblab.me`.
- Org runners run whatever any org repo's pull request workflow says. Keep the repos private, or require approval for outside contributors' runs.

## Not Unity hosts: asus-laptop and hp-victus

Both are k3s nodes and, since 2026-11-03, light/heavy platform runners only. Unity load there broke the cluster along with the jobs: asus holds forgejo and the databases and ran at 90C, and hp-victus (31G) had 279 OOM kills in its Unity slices from 10-29 to 11-03, some of them litellm and prometheus when the whole host ran out. Their slots were removed with `scripts/unity-ci-runner-remove.sh`; the reasons are in `ansible/inventory.yml` above `unity_ci_runners`. Do not add either back.
