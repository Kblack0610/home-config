"""A host's identity as the inventory states it: the names other machines dial it by, its ssh host
key (ssh_host_ed25519 in ansible/inventory.yml) and the NIC its DHCP reservation pins
(infrastructure/dhcp/devices.yaml). The role writes the roster's keys into /etc/ssh/ssh_known_hosts
on every roster machine and fails a host whose live key or NICs disagree with those two files.

Stdlib only: tests/test_host_identity.py runs it in the bare CI container.
"""

import re

# The names a host's NICs carry on our Linux hosts (enp16s0, eth0, wlan0, wlp3s0). Only these are
# listed in a report; every interface is still compared, so a bridge or veth never hides a match.
NIC_NAME = re.compile(r"^(en|eth|wl)")


def roster_hosts(groups, roster_groups):
    """The inventory hosts in any of roster_groups, sorted. A group the inventory lacks adds none."""
    return sorted({h for g in roster_groups for h in (groups.get(g) or [])})


def host_device(devices, name, hv):
    """The devices.yaml entry for inventory host name (hv: its vars): the one whose ip is its
    ansible_host, else the one whose hostname is its fleet_ssh_host's first label or its inventory
    name. None when no entry matches."""
    devices = devices or []
    addr = str(hv.get("ansible_host") or "")
    for d in devices:
        if addr and str(d.get("ip") or "") == addr:
            return d
    labels = {name, str(hv.get("fleet_ssh_host") or "").split(".")[0]}
    for d in devices:
        if d.get("hostname") and d["hostname"] in labels:
            return d
    return None


def host_names(name, hv, device=None):
    """Every name a roster machine may be dialed by, as known_hosts writes it: its inventory name,
    ansible_host, fleet_ssh_host and ssh_aliases, its DHCP name and IP, and both names with .lan.
    Whichever name the fleet dials it by is trusted. "[name]:port" when sshd is not on 22."""
    raw = [name, name + ".lan", hv.get("ansible_host"), hv.get("fleet_ssh_host")] + list(hv.get("ssh_aliases") or [])
    if device and device.get("hostname"):
        raw += [device["hostname"], device["hostname"] + ".lan", device.get("ip")]
    port = int(hv.get("ansible_port") or 22)
    out = []
    for n in raw:
        n = str(n or "").strip().lower()
        if n and n not in out:
            out.append(n)
    return out if port == 22 else [f"[{n}]:{port}" for n in out]


def _key(value):
    """"ssh-ed25519 AAAA..." from a public key line, its comment dropped; "" when not one."""
    parts = str(value or "").split()
    return " ".join(parts[:2]) if len(parts) >= 2 and parts[0] == "ssh-ed25519" else ""


def roster_known_hosts(hostvars, groups, roster_groups, devices):
    """The known_hosts lines for every roster host that has an ssh_host_ed25519, one a host, sorted
    by name. A host without one gets no line; identity_problems names it on that host."""
    lines = []
    for h in roster_hosts(groups, roster_groups):
        hv = hostvars[h]
        key = _key(hv.get("ssh_host_ed25519"))
        if key:
            lines.append(",".join(host_names(h, hv, host_device(devices, h, hv))) + " " + key)
    return lines


def identity_problems(facts, name, hv, device, in_roster):
    """What on this host disagrees with the inventory, one sentence each, saying what to change.
    facts: the host's gathered ansible_facts. The host key is checked only on a roster machine;
    the NIC on any host whose devices.yaml entry reserves a MAC."""
    out = []
    if in_roster:
        live = str(facts.get("ssh_host_key_ed25519_public") or "")
        want = _key(hv.get("ssh_host_ed25519"))
        if not live:
            out.append(f"sshd on {name} has no ed25519 host key, so no roster machine can trust it")
        elif want.split(" ")[-1] != live:
            verb = "is not the" if want else "is missing from the"
            out.append(f"{name}'s ssh host key {verb} inventory's: set ssh_host_ed25519: "
                       f"ssh-ed25519 {live} on {name} in ansible/inventory.yml")
    mac = str((device or {}).get("mac") or "").upper()
    if mac:
        nics = {}
        for iface in facts.get("interfaces") or []:
            m = str((facts.get(iface.replace("-", "_")) or {}).get("macaddress") or "").upper()
            if m:
                nics[iface] = m
        if mac not in nics.values():
            have = ", ".join(f"{i} {m}" for i, m in sorted(nics.items()) if NIC_NAME.match(i)) or "none"
            out.append(f"devices.yaml reserves {device.get('ip')} ({device.get('hostname')}) for {mac}, "
                       f"which no NIC on {name} has (its NICs: {have}): set that mac in "
                       f"infrastructure/dhcp/devices.yaml, then dhcp sync")
    return out


class FilterModule:
    def filters(self):
        return {
            "roster_hosts": roster_hosts,
            "host_device": host_device,
            "roster_known_hosts": roster_known_hosts,
            "identity_problems": identity_problems,
        }
