#!/bin/sh
# Nightly backup of the Zomboid world to the NAS.
#
# Destination is the NAS *public* share, which is `guest ok=yes, writable=yes`
# (apps/nas/deployment.yaml). That is deliberate: it means this job needs no
# credentials at all, so the NAS password is not duplicated into a second
# namespace where it would drift from the copy in apps/nas.
#
# A world save is not sensitive - it holds no keys or personal data - so the
# public share is an appropriate home for it.
set -eu

NAS_HOST="${NAS_HOST:?}"
NAS_SHARE="${NAS_SHARE:-public}"
DEST_DIR="${DEST_DIR:-backups/zomboid}"
# Everything is expressed relative to DATA_DIR so the paths that get CHECKED and
# the paths that get ARCHIVED cannot drift apart.
DATA_DIR="${DATA_DIR:-/data}"
SAVE_SUBPATH="${SAVE_SUBPATH:-Saves/Multiplayer/servertest}"
SERVER_SUBPATH="${SERVER_SUBPATH:-Server}"
WORK_DIR="${WORK_DIR:-/tmp}"
KEEP="${KEEP:-14}"

SAVE_DIR="${DATA_DIR}/${SAVE_SUBPATH}"
SERVER_DIR="${DATA_DIR}/${SERVER_SUBPATH}"

STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="zomboid-${STAMP}.tar.gz"

log() { echo "[$(date -Iseconds)] $*"; }

# --- refuse to back up nothing -------------------------------------------------
# An empty or missing save dir means the volume is not mounted where we think it
# is. Writing a 200-byte "successful" tarball over a good backup set is worse
# than failing loudly, and this is the failure mode that hides for weeks.
if [ ! -d "${SAVE_DIR}" ]; then
  log "ERROR: ${SAVE_DIR} does not exist - is the PVC mounted? Refusing to back up."
  exit 1
fi
entries="$(find "${SAVE_DIR}" -mindepth 1 -maxdepth 1 | wc -l)"
if [ "${entries}" -eq 0 ]; then
  log "ERROR: ${SAVE_DIR} is empty. Refusing to write an empty backup."
  exit 1
fi
log "world has ${entries} top-level entries"

# --- consistency note ----------------------------------------------------------
# This copies the save without first asking the server to flush. In practice that
# is fine and it is what every PZ backup guide does: the schedule (04:30) lands
# while the server is almost always scaled to zero, in which case the on-disk save
# is completely quiescent and the copy is exact. If someone happens to be playing,
# the worst case is a backup as stale as the last autosave, since
# SaveWorldEveryMinutes=15.
#
# Deliberately NOT issuing an RCON `save` first: the RCON client lives in
# sleep/sleeper.py, and kustomize's path restrictions make sharing one copy across
# two ConfigMaps awkward, so wiring it in here would mean a second implementation
# of the same protocol. That is the kind of duplication that drifts. If quiesced
# backups become necessary, the right fix is to hoist both scripts into a single
# shared ConfigMap, not to copy the client.

# --- archive -------------------------------------------------------------------
cd "${WORK_DIR}"
tar czf "${ARCHIVE}" -C "${DATA_DIR}" "${SAVE_SUBPATH}" "${SERVER_SUBPATH}"
size="$(wc -c < "${ARCHIVE}")"
log "created ${ARCHIVE} (${size} bytes)"

if [ "${size}" -lt 1048576 ]; then
  log "ERROR: archive is under 1MB, which is implausible for a PZ world. Not uploading."
  exit 1
fi

# --- upload --------------------------------------------------------------------
SMB="//${NAS_HOST}/${NAS_SHARE}"
smbclient "${SMB}" -N -c "mkdir ${DEST_DIR}" >/dev/null 2>&1 || true
smbclient "${SMB}" -N -c "cd ${DEST_DIR}; put ${ARCHIVE} ${ARCHIVE}"
log "uploaded to ${SMB}/${DEST_DIR}/${ARCHIVE}"

# --- verify it actually landed, at the right size ------------------------------
remote_size="$(smbclient "${SMB}" -N -c "cd ${DEST_DIR}; ls ${ARCHIVE}" 2>/dev/null \
  | awk -v f="${ARCHIVE}" '$1==f {print $3}')"
if [ -z "${remote_size}" ]; then
  log "ERROR: ${ARCHIVE} is not on the share after upload."
  exit 1
fi
if [ "${remote_size}" != "${size}" ]; then
  log "ERROR: size mismatch - local ${size}, remote ${remote_size}."
  exit 1
fi
log "verified on the share: ${remote_size} bytes"

# --- prune ---------------------------------------------------------------------
# Sorted by name, which is chronological because the stamp is YYYYmmdd-HHMMSS.
olds="$(smbclient "${SMB}" -N -c "cd ${DEST_DIR}; ls zomboid-*.tar.gz" 2>/dev/null \
  | awk '/^ *zomboid-.*\.tar\.gz/ {print $1}' | sort)"
count="$(echo "${olds}" | grep -c . || true)"
if [ "${count}" -gt "${KEEP}" ]; then
  drop="$(echo "${olds}" | head -n "$((count - KEEP))")"
  for f in ${drop}; do
    smbclient "${SMB}" -N -c "cd ${DEST_DIR}; del ${f}" >/dev/null 2>&1 \
      && log "pruned ${f}"
  done
fi
log "done - ${count} backup(s) on the share, keeping ${KEEP}"
