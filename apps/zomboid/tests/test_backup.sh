#!/usr/bin/env bash
# Tests for the save-backup script.
#
# The guards are the point. A backup job that "succeeds" while writing nothing is
# the failure mode that hides for weeks and is only discovered when you need the
# backup - so every case below asserts it REFUSES rather than that it works.
#
# Run: apps/zomboid/tests/test_backup.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="${HERE}/../backup/backup.sh"
FAILED=0

check() { # label expected_rc actual_rc
  if [[ "$2" == "$3" ]]; then echo "  PASS  $1 (rc=$3)"; else
    echo "  FAIL  $1 (want rc=$2, got rc=$3)"; FAILED=1; fi
}

# Stub smbclient so nothing touches the real NAS, and record what it was asked.
setup() {
  SANDBOX="$(mktemp -d)"
  mkdir -p "${SANDBOX}/bin"
  cat > "${SANDBOX}/bin/smbclient" <<'STUB'
#!/bin/sh
# Models real smbclient closely enough to catch the bug that shipped once:
#  - mkdir does NOT create parent directories
#  - a failed `cd` prints NT_STATUS_* and the REST OF THE -c STRING STILL RUNS
#  - the process still exits 0 either way
cmd=""
while [ $# -gt 0 ]; do case "$1" in -c) cmd="$2"; shift 2;; *) shift;; esac; done
echo "$cmd" >> "${SMB_CALLS}"
DIRS="${SMB_DIRS}"; touch "$DIRS"
run_one() {
  case "$1" in
    "mkdir "*)
      d=$(echo "$1" | sed 's/^mkdir //')
      parent=$(dirname "$d")
      if [ "$parent" != "." ] && ! grep -qx "$parent" "$DIRS"; then
        echo "mkdir \\$d: NT_STATUS_OBJECT_PATH_NOT_FOUND"; return
      fi
      if grep -qx "$d" "$DIRS"; then echo "NT_STATUS_OBJECT_NAME_COLLISION making remote directory"; return; fi
      echo "$d" >> "$DIRS" ;;
    "cd "*)
      d=$(echo "$1" | sed 's/^cd //')
      if grep -qx "$d" "$DIRS"; then CWD="$d"; else echo "cd \\$d\\: NT_STATUS_OBJECT_PATH_NOT_FOUND"; fi ;;
    "put "*) echo "putting file" ; echo "${CWD:-<ROOT>}|$1" >> "${SMB_PUTS}" ;;
    "ls "*)  echo "  $(echo "$1" | sed 's/^ls //')  A  ${FAKE_REMOTE_SIZE:-0}  Wed Aug 12 00:00:00 2026" ;;
  esac
}
CWD=""
OLDIFS="$IFS"; IFS=";"
for part in $cmd; do
  p=$(echo "$part" | sed 's/^ *//; s/ *$//')
  [ -n "$p" ] && run_one "$p"
done
IFS="$OLDIFS"
exit 0
STUB
  chmod +x "${SANDBOX}/bin/smbclient"
  export SMB_CALLS="${SANDBOX}/smb-calls.txt"; : > "${SMB_CALLS}"
  export SMB_DIRS="${SANDBOX}/smb-dirs.txt"; : > "${SMB_DIRS}"
  export SMB_PUTS="${SANDBOX}/smb-puts.txt"; : > "${SMB_PUTS}"
  export PATH="${SANDBOX}/bin:${PATH}"
  export NAS_HOST=nas.invalid
  export DATA_DIR="${SANDBOX}/data" WORK_DIR="${SANDBOX}/work"
  mkdir -p "${WORK_DIR}"
  SAVE_DIR="${DATA_DIR}/Saves/Multiplayer/servertest"
  SERVER_DIR="${DATA_DIR}/Server"
}
teardown() { rm -rf "${SANDBOX}"; }

echo "guards - must refuse:"

setup
run_rc() { ( cd "${SANDBOX}" && sh "${SCRIPT}" >/dev/null 2>&1 ); echo $?; }

# 1. save dir absent entirely (PVC not mounted)
check "missing save dir is refused" 1 "$(run_rc)"

# 2. save dir present but empty (mounted at the wrong path)
mkdir -p "${SAVE_DIR}" "${SERVER_DIR}"
check "empty save dir is refused" 1 "$(run_rc)"

# 3. save dir has content but the archive comes out implausibly small
echo "tiny" > "${SAVE_DIR}/map_meta.bin"
check "implausibly small archive is refused" 1 "$(run_rc)"

# nothing should have been uploaded in any of the three refusals
# grep -c prints 0 AND exits 1 on no match, so `|| echo 0` would emit a second line.
uploads="$(grep -c "put " "${SMB_PUTS}" 2>/dev/null | head -1)"
uploads="${uploads:-0}"
if [[ "${uploads}" == "0" ]]; then echo "  PASS  no upload attempted in any refusal case"; else
  echo "  FAIL  ${uploads} upload(s) attempted despite refusing"; FAILED=1; fi
teardown

echo
echo "happy path - must archive, upload and verify:"
setup
mkdir -p "${SAVE_DIR}" "${SERVER_DIR}"
# >1MB of incompressible data so the size guard passes for the right reason
head -c 3000000 /dev/urandom > "${SAVE_DIR}/map_meta.bin"
echo "x" > "${SERVER_DIR}/servertest.ini"
# make the stub report whatever size the script actually produced
export FAKE_REMOTE_SIZE_DYNAMIC=1
out="$( cd "${SANDBOX}" && sh "${SCRIPT}" 2>&1 )"; rc=$?
localsize="$(echo "${out}" | sed -n 's/.*created .* (\([0-9]*\) bytes).*/\1/p')"
FAKE_REMOTE_SIZE="${localsize}" out2="$( cd "${SANDBOX}" && FAKE_REMOTE_SIZE="${localsize}" sh "${SCRIPT}" 2>&1 )"; rc2=$?
check "backs up successfully when the world is real" 0 "${rc2}"
if grep -q "put " "${SMB_PUTS}"; then echo "  PASS  upload was attempted"; else
  echo "  FAIL  no upload attempted"; FAILED=1; fi
if echo "${out2}" | grep -q "verified on the share"; then echo "  PASS  size verified against the share"; else
  echo "  FAIL  did not verify remote size"; FAILED=1; fi
# THE assertion. The original version of this script uploaded to the share ROOT
# because `cd backups/zomboid` failed and smbclient carried on regardless, and every
# other check here still passed. Assert the destination, not just that a put happened.
landed="$(cut -d'|' -f1 "${SMB_PUTS}" | tail -1)"
if [[ "${landed}" == "backups/zomboid" ]]; then
  echo "  PASS  uploaded INTO backups/zomboid (not the share root)"
else
  echo "  FAIL  uploaded into '${landed}' instead of backups/zomboid"; FAILED=1; fi
teardown

echo
echo "negative control - a size mismatch must NOT pass:"
setup
mkdir -p "${SAVE_DIR}" "${SERVER_DIR}"
head -c 3000000 /dev/urandom > "${SAVE_DIR}/map_meta.bin"
rc3="$( cd "${SANDBOX}" && FAKE_REMOTE_SIZE=12 sh "${SCRIPT}" >/dev/null 2>&1; echo $? )"
check "remote size mismatch is refused" 1 "${rc3}"
teardown

echo
if [[ ${FAILED} -ne 0 ]]; then echo "FAILURES"; exit 1; fi
echo "all checks passed"
