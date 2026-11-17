#!/bin/sh
# Nightly restore set for the home-k3s server (pi5-master, k8s node raspberrypi),
# to the NAS: backups/home-k3s/k3s-server/<UTC date>/{k3s-server-backup.tgz,SHA256SUMS}.
# The archive holds, flat: state.db (the kine datastore), token, tls/,
# k3s.service and k3s.service.env. Restore: docs/backup-runbook.md,
# "Restore the k3s server".
#
# Mounts (cronjob.yaml): /k3s/db is /var/lib/rancher/k3s/server/db, read-write
# only so sqlite can open the WAL index beside the live DB; /k3s/token and
# /k3s/tls read-only; /unit holds the two systemd files read-only; /work is an
# emptyDir that holds the snapshot only until it is archived.
set -eu

# stderr, so a message from inside smb() survives its callers' >/dev/null.
log() { echo "[$(date -Iseconds)] $*" >&2; }
die() { log "FATAL: $*"; exit 1; }

apk add --no-cache sqlite samba-client >/dev/null

SMB="//nas.nas.svc.cluster.local/private"
DEST="backups/home-k3s/k3s-server/$(date -u +%F)"
ARCHIVE=k3s-server-backup.tgz
STAGE=/work/stage
# The live DB holds about 120 MB of rows and the archive is about 22 MB
# (2026-11-16). 1 MB catches an empty or unmounted source.
MIN_BYTES=1048576

# The snapshot is a full copy of the datastore and pi5-master's root is an SD
# card: never leave it behind, whatever the exit.
trap 'rm -rf /work/stage /work/${ARCHIVE} /work/${ARCHIVE}.partial /work/SHA256SUMS' EXIT
rm -rf "${STAGE}" "/work/${ARCHIVE}" "/work/${ARCHIVE}.partial" /work/SHA256SUMS
mkdir -p "${STAGE}"

# VACUUM INTO, never .backup. kine writes constantly, and the online-backup
# API behind .backup restarts on every write from another connection: on
# 2026-11-16 it ran 3 hours on this DB without finishing. VACUUM INTO copies
# one read-transaction snapshot in seconds.
[ -f /k3s/db/state.db ] || die "/k3s/db/state.db missing; is this pod on the k3s server?"
sqlite3 -cmd '.timeout 60000' /k3s/db/state.db "VACUUM INTO '${STAGE}/state.db'"
check="$(sqlite3 "${STAGE}/state.db" 'PRAGMA integrity_check')"
[ "${check}" = "ok" ] || die "integrity_check on the snapshot: ${check}"
rows="$(sqlite3 "${STAGE}/state.db" 'SELECT count(*) FROM kine')"
maxid="$(sqlite3 "${STAGE}/state.db" 'SELECT max(id) FROM kine')"
[ "${rows}" -gt 1000 ] || die "snapshot holds ${rows} kine rows, implausible for a live cluster"
log "snapshot: $(wc -c < "${STAGE}/state.db") bytes, ${rows} kine rows, max id ${maxid}, integrity ok"

cp -p /k3s/token "${STAGE}/token"
cp -pR /k3s/tls "${STAGE}/tls"
cp -p /unit/k3s.service /unit/k3s.service.env "${STAGE}/"

tar -czf "/work/${ARCHIVE}.partial" -C "${STAGE}" .
rm -rf "${STAGE}"
gzip -t "/work/${ARCHIVE}.partial"
size="$(wc -c < "/work/${ARCHIVE}.partial")"
[ "${size}" -ge "${MIN_BYTES}" ] || die "archive is ${size} bytes"
mv "/work/${ARCHIVE}.partial" "/work/${ARCHIVE}"
(cd /work && sha256sum "${ARCHIVE}" > SHA256SUMS)
log "archive: ${size} bytes, $(cut -d' ' -f1 /work/SHA256SUMS)"

# The NAS is the only copy, so a failed upload fails the job
# (docs/backup-runbook.md, failure policy). smbclient -c does not exit
# non-zero when a command inside it fails, so every call is checked for
# NT_STATUS_. Why, and the identical copies that move with this one:
# apps/zomboid/backup/backup.sh.
smb() {
  _out="$(smbclient "${SMB}" -U "nas%${NAS_SMB_PASSWORD}" -c "$1" 2>&1)"
  _bad="$(echo "${_out}" | grep -o 'NT_STATUS_[A-Z_]*' | grep -v 'NT_STATUS_OBJECT_NAME_COLLISION' || true)"
  if [ -n "${_bad}" ]; then
    log "ERROR: smbclient '$1' failed: ${_bad}"
    return 1
  fi
  echo "${_out}"
}

# mkdir does not create parents, so walk the path a component at a time.
# OBJECT_NAME_COLLISION means it already exists.
_path=""
for _part in $(echo "${DEST}" | tr '/' ' '); do
  _path="${_path:+${_path}/}${_part}"
  smb "mkdir ${_path}" >/dev/null
done

# Upload under .partial and rename over only once the size matches, so a
# second run on the same day never leaves a broken archive in place of a good
# one. SHA256SUMS goes last: when it matches, the directory is whole.
smb "cd ${DEST}; put /work/${ARCHIVE} ${ARCHIVE}.partial" >/dev/null
remote_size="$(smb "cd ${DEST}; ls ${ARCHIVE}.partial" | awk -v f="${ARCHIVE}.partial" '$1==f {print $3}')"
[ "${remote_size}" = "${size}" ] || die "size on the share is '${remote_size}', local ${size}"
smb "cd ${DEST}; rename ${ARCHIVE}.partial ${ARCHIVE} -f; put /work/SHA256SUMS SHA256SUMS" >/dev/null
smb "cd ${DEST}; ls ${ARCHIVE}" | grep -q "^ *${ARCHIVE} " || die "${ARCHIVE} missing after rename"
log "NAS copy OK: ${SMB}/${DEST}/${ARCHIVE} (${size} bytes)"
