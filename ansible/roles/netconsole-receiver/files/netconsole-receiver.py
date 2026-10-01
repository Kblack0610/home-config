#!/usr/bin/env python3
"""Receive netconsole kernel messages over UDP and print them to stdout.

systemd hands stdout to journald, which stamps each line with the time it
arrived. That receive time matters: the sending Pis have no RTC, and this
kernel is built without CONFIG_NETCONSOLE_EXTENDED_LOG, so the messages carry
no wall-clock time of their own.

Usage: netconsole-receiver.py <port> <allowed sender IP>...
"""
import socket
import sys


def main() -> None:
    port = int(sys.argv[1])
    allowed = set(sys.argv[2:])
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    print(f"listening on udp/{port} for {' '.join(sorted(allowed))}", flush=True)
    while True:
        data, (ip, _) = sock.recvfrom(65535)
        if ip not in allowed:
            continue
        for line in data.decode("utf-8", "replace").splitlines():
            if line.strip():
                print(f"{ip} {line}", flush=True)


if __name__ == "__main__":
    main()
