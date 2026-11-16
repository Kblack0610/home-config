# Node OOM-kill and core-dump storms

`NodeOomKillStorm` (10 or more OOM kills in an hour) and `NodeCoreDumpStorm` (more than 5 core dumps in an hour, or over 10 GB of stored dumps) page critical and carry `fleet: fix`, so each one also becomes a `red` queue item for an agent (see [node-disk-pressure.md](node-disk-pressure.md#when-it-pages)). The node count comes from node-exporter's `node_vmstat_oom_kill` and from `ansible/roles/node-disk-metrics`, so the alert names a host, never a process. The job is to name the process, find the owner of the thing that launches it, and fix or queue it there.

SSH by IP or `-lan` name ([host-access.md](../host-access.md)): razer-laptop 192.168.1.26, asus-laptop 192.168.1.152, hp-victus 192.168.1.243.

## Core dumps: which process

1. Count by executable and unit: `coredumpctl list --since -6h --no-pager`, then `coredumpctl info -1 --no-pager | grep -E 'Command Line|Signal|Unit|Control Group'`. The unit names the runner slot or service that launched it.
2. What holds the bytes: `ls -lS /var/lib/systemd/coredump | head`. Unity cores are 0.2 to 1.4 GB each; a few of them pass the 10 GB line on their own.
3. A CI runner unit (`actions.runner.<host>-<slot>.service`): the job is in the slot's newest worker log, `sudo ls -t /var/lib/linux-ci/<slot>/_diag/Worker_* | head -1`, and grep it for `Processing step: DisplayName`.
4. Stored dumps are safe to delete once the cause is known: `sudo find /var/lib/systemd/coredump -name 'core.Unity.*' -mtime +2 -delete`.

## OOM kills: which process and cgroup

1. Group the kernel's records: `journalctl -k --since -1d --no-pager | grep -oE 'oom-kill:.*task=[^,]+' | sed -E 's/.*oom_memcg=([^,]+).*task=(.*)/\1 \2/' | sort | uniq -c | sort -rn | head`. `oom_memcg` is the limit that was hit; a `kubepods` path is a pod at its own limit, a `ci.slice` path is a runner slice, `/` is the whole node.
2. A pod: find the owner across the cluster: `count by (namespace, created_by_name, node) (max_over_time((kube_pod_container_status_terminated_reason{reason="OOMKilled"} * on(namespace,pod) group_left(created_by_name, node) kube_pod_info)[1d:15m]))`. A Job pod is never restarted, so its OOM shows only in `terminated_reason`.
3. A runner slice: `ps -o user= -C ssh-agent | sort | uniq -c` first; leaked agents fill a slice over days.

## A host hung, rebooted or went NotReady

1. `scripts/host-postmortem.py <host> <time>` (ISO, or `now`) prints the hour before: a 5-minute metrics table (CPU, load, memory, swap, pressure, NIC traffic and errors, CPU and NVMe temperature), the last successful scrape, kernel lines matching each `host-kernel-faults` Loki rule, the last line the host shipped, and its netconsole lines on pi5-master.
2. Netconsole lines after the last shipped line are the host's last words: the x86 nodes and the Pis in `netconsole_senders` send them (roles/crash-evidence).
3. A boot that ended without a shutdown and with `kernel.panic`/watchdog armed (roles/hw-watchdog, `watchdog_hosts`) rebooted itself; `journalctl --list-boots` on the host shows the gap.

## Known causes

| Alert | Process | Launched by | Owner |
|---|---|---|---|
| NodeCoreDumpStorm on every `light-*` runner host | `sleep 120`, SIGABRT | unity-core-playground harness tests: the fake editor in `scripts/test/tests/test_run.py` is aborted by the watchdog in `scripts/test/run.sh` | board row ucp-1836: the fake editor needs `ulimit -c 0` |
| Host hang after `GSP RM heartbeat timed out` (HostGpuFirmwareFault) and an NVMe drop at 77-80C (NodeNvmeHot) | NVIDIA GSP firmware on hp-victus's RTX 4060 | the nvidia driver, GPU unused | dGPU switched off by roles/dgpu-off (#947) |
| Laptop node drops off the LAN at idle, no kernel or netconsole line (NodeOnBattery, NodeBatteryLow) | the battery ran flat: hp-victus came back from a power-cycle on 2026-11-14 with its charger out (`mains 0`, `bat%` falling in host-postmortem) | the human, at the power-cycle | plug the charger in and seat it |
| NodeCoreDumpStorm by bytes | `Unity` | crashed Unity editors on CI slots | delete old dumps (step 4 above) |
| NodeOomKillStorm on razer and asus | `python` in `freqtrade/trader-markets-recorder` (Kalshi) | CronJob every 5 min (`apps/freqtrade/markets-recorder-cronjob.yaml`), sized from a measured peak; re-measure in a one-off Job with a higher limit before changing it | tr-184: page-scoped state reads cut a full 171,144-market sweep to 218,808 KiB RSS on ASUS (2026-11-11), down from 1,894 MB with full-venue reads; verify the image includes trader #125 before using the 1Gi limit |
| NodeOomKillStorm on CI hosts | `ssh-agent` in `ci.slice` | jobs that start an agent and never kill it | board row hc-172 |
