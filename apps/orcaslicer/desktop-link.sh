#!/usr/bin/env bash
# Point a desktop OrcaSlicer at the presets in this repo (apps/orcaslicer/profiles).
#
# - user/default/filament and user/default/process become symlinks to the repo
#   folders, so a preset saved on the desktop is a change in `git status`.
# - user/default/machine stays a real folder: the Neptune presets are symlinked
#   one by one, and the A1 preset is written as a local copy with its LAN code and
#   serial filled from the SOPS secret, so the code never lands in the repo.
# - A fresh OrcaSlicer.conf is seeded from the k3s one (wizard done, vendors on).
# - download and export folders point at the NAS prints tree.
#
# Idempotent. Run with OrcaSlicer closed. Usage: desktop-link.sh
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
profiles="$here/profiles"
secret="$here/bambu-a1-secret.yaml"
conf_dir="${ORCA_CONFIG_DIR:-$HOME/.config/OrcaSlicer}"
user_dir="$conf_dir/user/default"
prints="${ORCA_PRINTS_DIR:-$HOME/3d-prints}"
stamp=$(date +%F)

if pgrep -x orca-slicer >/dev/null; then
  echo "OrcaSlicer is running; close it first." >&2
  exit 1
fi

mkdir -p "$user_dir/machine"

# Fill the @A1_...@ placeholders in the files given, from the SOPS secret.
fill_a1() {
  local code serial
  code=$(sops -d --extract '["stringData"]["access-code"]' "$secret")
  serial=$(sops -d --extract '["stringData"]["serial"]' "$secret")
  sed -i -e "s/@A1_ACCESS_CODE@/$code/g" -e "s/@A1_SERIAL@/$serial/g" "$@"
}

for kind in filament process; do
  target="$user_dir/$kind"
  if [ -d "$target" ] && [ ! -L "$target" ]; then
    if [ -n "$(ls -A "$target")" ]; then
      mv "$target" "$target.pre-link-$stamp"
      echo "moved old $kind presets to $target.pre-link-$stamp"
    else
      rmdir "$target"
    fi
  fi
  ln -sfn "$profiles/$kind" "$target"
done

for src in "$profiles"/machine/*; do
  dest="$user_dir/machine/$(basename "$src")"
  if grep -q '@A1_' "$src"; then
    rm -f "$dest"
    cp "$src" "$dest"
    chmod 600 "$dest"
    fill_a1 "$dest"
  else
    ln -sfn "$src" "$dest"
  fi
done

conf="$conf_dir/OrcaSlicer.conf"
if [ ! -f "$conf" ]; then
  cp "$here/OrcaSlicer.conf" "$conf"
  chmod 600 "$conf"
  fill_a1 "$conf"
  echo "seeded $conf"
fi
tmp=$(mktemp)
jq --arg inbox "$prints/_inbox" --arg prints "$prints" '
  .app.download_path = $inbox
  | .app.last_export_path = $prints
  | .recent.last_opened_folder = $prints
  | .recent.settings_folder = $prints' "$conf" > "$tmp"
cat "$tmp" > "$conf"
rm -f "$tmp"

echo "linked $user_dir -> $profiles"
