# login-passwords

Sets the login user's password on the bare-metal Linux hosts from a hash held in ansible-vault, and locks root's password.

## Why

The PXE installer seeds both root and the login user with `changeme` (`infrastructure/pxe-server/http/kickstart/disk-install.sh`, `auto-provision.sh`) so a fresh box can be reached at the console on first boot. Nothing replaced it afterwards: razer-laptop still had `changeme` on both accounts a day after joining CI, and hp-victus's and asus-laptop's passwords were whatever someone last typed by hand. This role makes Ansible the owner, so the first `site.yml` run after a reinstall closes the gap.

Root is locked rather than given a password. ssh refuses root (`PermitRootLogin no`) and the login user's sudo is NOPASSWD, so nothing needs one. The cost: systemd's emergency shell will not open for a locked root. To get a root shell on a box that will not boot, add `init=/bin/bash` (or `systemd.setenv=SYSTEMD_SULOGIN_FORCE=1`) to the kernel command line at the boot menu.

SOPS in this repo encrypts Kubernetes Secrets only (`.sops.yaml`); Ansible's secrets live in ansible-vault, so the hash goes there.

## Set or rotate the password

Run these yourself; the plain text never goes into a file or a chat.

```bash
openssl passwd -6          # prompts twice, prints $6$...
cd ~/dev/home/home-config/ansible
ansible-vault edit group_vars/linux_bare_metal/vault.yml
# add:  vault_linux_login_password_hash: '$6$...'
ansible-playbook playbooks/site.yml --limit linux_bare_metal --tags passwords --check --diff
ansible-playbook playbooks/site.yml --limit linux_bare_metal --tags passwords
```

Single quotes around the hash matter: it contains `$`.

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
