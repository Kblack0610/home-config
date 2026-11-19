# openwrt-base

Baseline settings on the OpenWrt router (ASUS TUF-AX6000, MT7986, OpenWrt 23.05.4) that are not part of a phased migration.

## TSO

Hardware TCP segmentation offload stays off on eth0 (CPU port to the LAN switch) and eth1 (WAN). With TSO on, the router corrupts forwarded TCP downloads and writes a valid checksum over the corrupted bytes, so no TCP checksum on the path catches it. Only TLS does: CI downloads failed with `bad_record_mac` / `bad decrypt` (hc-647, 2026-11-18).

Measured with `scripts/wan-integrity-probe.sh 6 6` on deskslim-1, deskslim-2 and ms-a2 at once (108 checksummed 160 MB downloads from dl.google.com per run):

| Router state | Failed | Wall time |
|---|---|---|
| stock (GRO on, TSO on) | 10/108, then 7/108 | 155 s |
| GRO off on eth1 | 0/108 | 300 s |
| GRO on, TSO off on eth0 | 0/108 | 174 s |

The router's own downloads were clean (6/6 via uclient-fetch), and with GRO off the hosts still counted no TCP checksum errors, so nothing arrives corrupt from the ISP. One host alone with 4 streams did not reproduce it (0/100); it needs forwarded load from several LAN hosts.

eth1 TSO is off on the same reasoning (uploads take the same driver path in the other direction); it was not measured separately. Features reset at boot, so `/etc/hotplug.d/iface/90-tso-off` turns TSO off again on every ifup, using the device list in `/etc/tso-off-devices`.

Before turning TSO back on (a newer OpenWrt release with mtk_soc_eth fixes), run the probe from three hosts with it on.

## Remote log

logd sends every line over UDP to the netconsole receiver on pi5-master (`roles/netconsole-receiver`), so the 64 KB ring on the router is not the only copy:

```bash
ssh kblack0610@192.168.1.20 'journalctl -u netconsole-receiver --since "-1h" | grep 192.168.1.1'
```

## Run it

```bash
cd ansible
ansible-playbook playbooks/site.yml --limit pi5-master --tags netconsole        # receiver allows the router first
ansible-playbook playbooks/site.yml --limit openwrt --tags openwrt-base --check --diff
ansible-playbook playbooks/site.yml --limit openwrt --tags openwrt-base
```
