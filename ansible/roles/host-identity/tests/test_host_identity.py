"""Offline tests for the host-identity filters: python3 ansible/roles/host-identity/tests/test_host_identity.py"""
import os
import sys
import unittest

import yaml


def load(path):
    with open(path) as f:
        return yaml.safe_load(f)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..", "..", "..", "..")
sys.path.insert(0, os.path.join(HERE, "..", "filter_plugins"))
sys.path.insert(0, os.path.join(REPO, "scripts"))
from host_identity import host_device, host_names, identity_problems, roster_hosts, roster_known_hosts  # noqa: E402
from host_inventory import load_inventory  # noqa: E402  the inventory reader every offline check shares

ROSTER = ["desktop_workstations", "fleet_workers", "fleet_controllers"]
KEY_A = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGaTLqfJOaCga5UzV+WsuxyRSRPcUY5GMUFloCREQ9aE"
KEY_B = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINmhQxBIidZJ6HtH0d5CRf4sj0l1CPYymCrvN/dcypAa"
DEVICES = [
    {"hostname": "pc-home-cachy-main", "mac": "9C:6B:00:AC:91:20", "ip": "192.168.1.2"},
    {"hostname": "razer-laptop", "mac": "A0:CE:C8:BA:3E:21", "ip": "192.168.1.26"},
]
HOSTVARS = {
    "cachyos-x8664-main": {"fleet_ssh_host": "pc-home-cachy-main.lan", "ssh_host_ed25519": KEY_A + " root@cachyos-x8664-main"},
    "razer-laptop": {"ansible_host": "192.168.1.26", "ssh_host_ed25519": KEY_B},
}
GROUPS = {"desktop_workstations": ["cachyos-x8664-main"], "fleet_workers": ["razer-laptop"], "all": list(HOSTVARS)}


def facts(key, **nics):
    f = {"ssh_host_key_ed25519_public": key.split()[1], "interfaces": list(nics)}
    for iface, mac in nics.items():
        f[iface.replace("-", "_")] = {"macaddress": mac}
    return f


class KnownHosts(unittest.TestCase):
    def test_every_dialed_name_of_each_roster_host(self):
        lines = roster_known_hosts(HOSTVARS, GROUPS, ROSTER, DEVICES)
        self.assertEqual(lines, [
            "cachyos-x8664-main,cachyos-x8664-main.lan,pc-home-cachy-main.lan,pc-home-cachy-main,192.168.1.2 " + KEY_A,
            "razer-laptop,razer-laptop.lan,192.168.1.26 " + KEY_B,
        ])

    def test_home_move_names_are_trusted_before_the_move(self):
        # 2026-11-15: the home moved to razer-laptop as razer-laptop.lan, and a worker never trusted it.
        names = roster_known_hosts(HOSTVARS, GROUPS, ROSTER, DEVICES)[1].split()[0].split(",")
        self.assertIn("razer-laptop.lan", names)

    def test_host_without_a_key_gets_no_line(self):
        hv = dict(HOSTVARS, **{"razer-laptop": {"ansible_host": "192.168.1.26"}})
        self.assertEqual(len(roster_known_hosts(hv, GROUPS, ROSTER, DEVICES)), 1)

    def test_port_other_than_22(self):
        self.assertEqual(host_names("asus-laptop", {"ansible_host": "192.168.1.152", "ansible_port": 2222}),
                         ["[asus-laptop]:2222", "[asus-laptop.lan]:2222", "[192.168.1.152]:2222"])

    def test_missing_group_adds_no_host(self):
        self.assertEqual(roster_hosts(GROUPS, ROSTER), ["cachyos-x8664-main", "razer-laptop"])


class Device(unittest.TestCase):
    def test_by_ip_then_by_name(self):
        self.assertEqual(host_device(DEVICES, "razer-laptop", HOSTVARS["razer-laptop"])["ip"], "192.168.1.26")
        self.assertEqual(host_device(DEVICES, "cachyos-x8664-main", HOSTVARS["cachyos-x8664-main"])["ip"], "192.168.1.2")
        self.assertIsNone(host_device(DEVICES, "pi5-master", {"ansible_host": "192.168.1.20"}))


class Problems(unittest.TestCase):
    def run_check(self, name, f, in_roster=True, hv=None):
        hv = HOSTVARS[name] if hv is None else hv
        return identity_problems(f, name, hv, host_device(DEVICES, name, hv), in_roster)

    def test_matching_host_has_none(self):
        self.assertEqual(self.run_check("razer-laptop", facts(KEY_B, **{"enp103s0f3u1c2": "a0:ce:c8:ba:3e:21", "wlp3s0": "3a:16:c9:98:03:3c"})), [])

    def test_reservation_on_an_old_nic(self):
        # 2026-11-16: the workstation's motherboard was swapped, and the reservation kept the old NIC's MAC.
        out = self.run_check("cachyos-x8664-main", facts(KEY_A, enp16s0="9c:6b:00:ac:91:99", docker0="b6:71:d3:5a:ff:71"))
        self.assertEqual(len(out), 1)
        self.assertIn("reserves 192.168.1.2 (pc-home-cachy-main) for 9C:6B:00:AC:91:20", out[0])
        self.assertIn("its NICs: enp16s0 9C:6B:00:AC:91:99)", out[0])

    def test_rebuilt_host_key(self):
        out = self.run_check("razer-laptop", facts(KEY_A, eth0="A0:CE:C8:BA:3E:21"))
        self.assertEqual(out, ["razer-laptop's ssh host key is not the inventory's: set ssh_host_ed25519: "
                               + KEY_A + " on razer-laptop in ansible/inventory.yml"])

    def test_roster_host_with_no_key_in_the_inventory(self):
        out = self.run_check("razer-laptop", facts(KEY_B, eth0="A0:CE:C8:BA:3E:21"), hv={"ansible_host": "192.168.1.26"})
        self.assertIn("is missing from the inventory's", out[0])

    def test_host_key_checked_only_on_roster_machines(self):
        self.assertEqual(self.run_check("razer-laptop", facts(KEY_A, eth0="A0:CE:C8:BA:3E:21"), in_roster=False), [])

    def test_no_reserved_mac_no_nic_check(self):
        self.assertEqual(identity_problems(facts(KEY_A, eth0="00:11:22:33:44:55"), "x", {}, {"ip": "192.168.1.1", "mac": ""}, False), [])


class RealInventory(unittest.TestCase):
    def test_every_roster_machine_has_a_trusted_key(self):
        hv, groups = load_inventory(os.path.join(REPO, "ansible", "inventory.yml"))
        devices = load(os.path.join(REPO, "infrastructure", "dhcp", "devices.yaml"))["devices"]
        roster = roster_hosts(groups, ROSTER)
        self.assertIn("cachyos-x8664-main", roster)
        lines = roster_known_hosts(hv, groups, ROSTER, devices)
        keyless = [h for h in roster if not any(l.startswith(h + ",") for l in lines)]
        self.assertEqual(keyless, [], "set ssh_host_ed25519 for these roster machines in ansible/inventory.yml")
        home = next(l for l in lines if l.startswith("cachyos-x8664-main,"))
        self.assertIn(hv["cachyos-x8664-main"]["fleet_ssh_host"], home.split()[0].split(","))

    def test_every_host_the_runner_checks_has_a_key(self):
        hv, groups = load_inventory(os.path.join(REPO, "ansible", "inventory.yml"))
        devices = load(os.path.join(REPO, "infrastructure", "dhcp", "devices.yaml"))["devices"]
        cron = open(os.path.join(REPO, "apps", "ansible-runner", "cronjob.yaml")).read()
        checked = [h for h in groups["linux_bare_metal"] if h + "=" in cron]
        self.assertGreaterEqual(len(checked), 5)
        lines = roster_known_hosts(hv, groups, ["linux_bare_metal"], devices)
        keyless = [h for h in checked if not any(l.startswith((h + ",", "[" + h + "]:")) for l in lines)]
        self.assertEqual(keyless, [], "set ssh_host_ed25519 for these hosts in ansible/inventory.yml")


if __name__ == "__main__":
    unittest.main()
