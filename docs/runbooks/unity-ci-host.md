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
- The first job on a new host proves it. If it says "Unity activation failed", in order: return stale activations on the Unity ID portal and rerun, run the host for platform CI only, or buy a paid seat or Build Server licenses. Licensing: [unity-ci-hardware.md](../unity-ci-hardware.md#unity-license).

## Keep the host's copies current

- `unity-ci-host` puts the asset-library-hd copy (`/var/lib/actions-runner/asset-library-hd`) and the editor images on each host, copied from the workstation. Re-run it with `--tags unity-ci-host` after asset-library-hd's packages change and on a Unity upgrade.
- Unity upgrade: add the new tags to `unity_ci_images` (`ansible/roles/unity-ci-host/defaults/main.yml`), `docker load` them on the workstation, and run the role on every host before the upgrade merges. The `windows-mono`, `mac-mono` and `android` tags player builds use belong there too, or the build dies pulling its image.
- By hand, when the role cannot run or the workstation's checkout holds an uncommitted change (the role packs all of `Packages/`; tar only the new package folders then), from the workstation (hp-victus has no rsync):
  - hp-victus: `tar -C ~/dev/bnb/games/engine/asset-library-hd -cf - Packages | ssh hp-victus 'sudo tar -C /var/lib/actions-runner/asset-library-hd -xf - && sudo chown -R actions-runner:actions-runner /var/lib/actions-runner/asset-library-hd'`
  - asus-laptop and razer-laptop: the same over `ssh -p 2222 kblack0610@192.168.1.152` and `ssh kblack0610@192.168.1.26`.
  - Then compare `ls Packages | wc -l` and `find Packages -type f | wc -l` on the host with the workstation's.

## Editor images over IPv4

`docker pull` of an editor image fails on the home network: the IPv6 path corrupts long downloads (`tls: bad record MAC`) and docker cannot resume a layer. Until the router is fixed, fetch over IPv4 on the workstation with `scripts/test/fetch-image.py`, which lives in the unity-core-playground repo (run it from that checkout), and load it over ssh:

```sh
scripts/test/fetch-image.py unityci/editor ubuntu-<version>-base-3 ~/.cache/unity-image-fetch/blobs ~/.cache/unity-image-fetch/base && tar -C ~/.cache/unity-image-fetch/base -cf - . | ssh hp-victus 'sudo docker load'
```

`<version>` is from the playground's `ProjectSettings/ProjectVersion.txt`; the other tags go the same way. Delete `~/.cache/unity-image-fetch` afterwards.

## Labels

- Every slot carries `unity`, plus `unity-full` while the host's `unity_ci_full` is true (the default). Full runs (nightly, release, Multi) ask for `unity-full`, merge gates for `unity`. GitHub has no queue priority, so to reserve a runner for gates, set `unity_ci_full: false` on one host.
- A runner keeps the labels it registered with. On a live one, `gh api -X POST orgs/BlackNBrownStudios/actions/runners/<id>/labels -f 'labels[]=unity-full'` adds a label and `gh api -X DELETE orgs/BlackNBrownStudios/actions/runners/<id>/labels/unity-full` removes it.
- The workstation has no slots and gets none; it takes merge gates only when the pool is backed up.

## The Mac pool

- The "Unity CI runner pool (macOS)" play (roles `unity-ci-host-mac` and `github-actions-runner-mac`, group `unity_ci_mac_runners`). Details: [mac-machines.md](../mac-machines.md#unity-ci-pool-mac-studio-until-it-is-sold).
- Slots register without GitHub's default labels, only `unity`, `unity-native`, `host-<name>` and `unity-full`: with `self-hosted` and `macOS` they would take the platform repo's iOS jobs and DodginBalls' `[self-hosted, unity]` game-ci jobs.
- Slots are LaunchAgents, and macOS Local Network privacy keeps Homebrew git and git-lfs off the LAN (a LaunchDaemon too), so the playground fetches through the public host, `git.kennethblack.me`.
- Retiring the Studio: steps in the macOS play's header comment in `site.yml`. Plan the capacity first; its slots are a large share of the pool.

## Secrets the playground workflows need

- `UNITY_SERIAL`, repo secret on unity-core-playground: DodginBalls' Personal serial (reading it from a `Unity_lic.ulf`: dodginballs `docs/operations/ci-findings.md`).
- Org secrets `UNITY_EMAIL`, `UNITY_PASSWORD` and `UNITY_CORE_DEPLOY_KEY` (the SSH deploy key for the unity-core submodule). The password is on Unity's command line inside the container, so `ps` on the host shows it.
- `ASSET_LIBRARY_TOKEN`: a Forgejo token with `read:repository` for the asset-library submodule on `git.kblab.me`.
- Org runners run whatever any org repo's pull request workflow says. Keep the repos private, or require approval for outside contributors' runs.

## hp-victus

hp-victus is also a k3s agent, the NFS backup target and a platform runner host. It has never had a full system upgrade (`pacman -Syu`), so a newly installed package can fail on a newer library (rsync failed on `libacl`, Xvfb on `libnettle`). The jobs run inside the editor image, which keeps them clear of that.
