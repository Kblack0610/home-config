# netconsole-receiver

Listens on udp/6666 on pi5-master and writes the kernel messages that `roles/crash-evidence` hosts send into this host's journal. See `roles/crash-evidence/README.md` for why.

pi5-master is the receiver because it is always on, is on the UPS, and is reached by IP, so the record survives a DNS outage and an outage of the sender.

- A stdlib-only Python listener: no rsyslog, which would also start writing `/var/log/syslog` on the control plane's SD card.
- Only source IPs of hosts in the `adguard` group are logged; other LAN traffic to the port is dropped.
- Each line is stamped with its receive time by journald. The senders have no RTC and their kernel has no extended netconsole format, so this is the only wall-clock time the messages get.
- Runs as a `DynamicUser` with no capabilities; the port is unprivileged.

```bash
journalctl -u netconsole-receiver -f
```
