#!/bin/sh
# Download the pinned web-kit (dotfiles .local/src/web-kit: tokens, fonts, <kb-shell>) into
# $1 and check every file against kit.lock: line 1 is the dotfiles commit, the rest is
# sha256sum output. A bad checksum fails the init container, so the pod never serves a
# kit it did not pin. Same contract as trader's web/kit.lock.
set -eu
lock=/files/kit.lock
dest=$1
commit=$(sed -n 's/^commit //p' "$lock")
sed '1d' "$lock" > "$dest/kit.sha256"
cd "$dest"
for name in $(awk '{print $2}' kit.sha256); do
  wget -q -T 30 -O "$name" "https://raw.githubusercontent.com/Kblack0610/.dotfiles/$commit/.local/src/web-kit/$name"
done
sha256sum -c kit.sha256
rm kit.sha256
