# login-passwords

Sets the login user's password on the bare-metal Linux hosts from a hash held in SOPS, and locks root's password.

## Why

The PXE installer seeds both root and the login user with `changeme` (`infrastructure/pxe-server/http/kickstart/disk-install.sh`, `auto-provision.sh`) so a fresh box can be reached at the console on first boot. Nothing replaced it afterwards: razer-laptop still had `changeme` on both accounts a day after joining CI, and hp-victus's and asus-laptop's passwords were whatever someone last typed by hand. This role makes Ansible the owner, so the first `site.yml` run after a reinstall closes the gap.

Root is locked rather than given a password. ssh refuses root (`PermitRootLogin no`) and the login user's sudo is NOPASSWD, so nothing needs one. The cost: systemd's emergency shell will not open for a locked root. To get a root shell on a box that will not boot, add `init=/bin/bash` (or `systemd.setenv=SYSTEMD_SULOGIN_FORCE=1`) to the kernel command line at the boot menu.

The password itself lives in rbw (`linux_fleet_password`), because a human types it. The repo holds only its hash, in `group_vars/linux_bare_metal/vault.sops.yaml`, because Ansible needs it unattended.

## Set or rotate the password

The plain text goes from rbw to `openssl` to `sops` through pipes, never a file, argv or the screen.

```bash
cd ~/dev/home/home-config
rbw generate 24 linux_fleet_password kblack0610 --no-symbols >/dev/null   # rotate: rbw edit instead
rbw get linux_fleet_password | openssl passwd -6 -stdin | jq -R . \
  | sops set --value-stdin ansible/group_vars/linux_bare_metal/vault.sops.yaml '["vault_linux_login_password_hash"]'
cd ansible
ansible-playbook playbooks/site.yml --limit linux_bare_metal --tags passwords --check --diff
ansible-playbook playbooks/site.yml --limit linux_bare_metal --tags passwords
```

## What it does

1. Asserts `vault_linux_login_password_hash` is a sha512 (`$6$`) or yescrypt (`$y$`) crypt hash, and fails the play otherwise.
2. Sets `ansible_user`'s password to that hash.
3. Sets root's password field to `!`.

All three are `no_log`, so a hash never reaches the output.

## Verify

```bash
ssh <host> 'sudo getent shadow root "$USER" | cut -d: -f1,2 | cut -c1-14'
# root:!
# kblack0610:$6$...
ssh <host> 'sudo -n true && echo sudo-ok'
```
