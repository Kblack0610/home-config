#!/bin/sh
# Managed by ansible (unity-ci-host-mac, tasks/signing-keychain.yml). Makes the Mac build keychain
# unity-core-playground's scripts/build/mac-sign.sh signs from exist, stay unlocked and sit in the
# user's keychain search list, so a reboot, a keychain reset or a hand edit of the list cannot drop it.
# The keychain path and password come from mac-signing.env, the one file mac-sign.sh reads too
# (unity-core-playground docs/operations/platform-builds.md#mac-signing). The Developer ID identity
# is not imported here: its private key is backed up by hand, and `mac-sign.sh --check` proves it.
# Prints CHANGED <what> per fix and OK when nothing moved. Tests: test-ensure-build-keychain.sh.
set -eu

ENV_FILE=${MAC_SIGNING_ENV:-"$HOME/.config/unity-core-playground/mac-signing.env"}
[ -r "$ENV_FILE" ] || { echo "error: $ENV_FILE missing; it holds MAC_SIGN_KEYCHAIN and MAC_SIGN_KEYCHAIN_PASSWORD" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
: "${MAC_SIGN_KEYCHAIN:?not set in $ENV_FILE}"
: "${MAC_SIGN_KEYCHAIN_PASSWORD:?not set in $ENV_FILE}"
kc=$MAC_SIGN_KEYCHAIN
changed=0
did() { echo "CHANGED $1"; changed=1; }

if [ ! -f "$kc" ]; then
    security create-keychain -p "$MAC_SIGN_KEYCHAIN_PASSWORD" "$kc"
    did "created $kc"
fi

# set-keychain-settings with no -l or -t clears the lock-on-sleep and lock-after-timeout rules, so
# an idle night does not lock it. It refuses a locked keychain ("User interaction is not allowed"),
# so unlock first, and again after in case the new settings relock it.
security unlock-keychain -p "$MAC_SIGN_KEYCHAIN_PASSWORD" "$kc"
security set-keychain-settings "$kc"
security unlock-keychain -p "$MAC_SIGN_KEYCHAIN_PASSWORD" "$kc"

# The user domain's list, one quoted path per line. Rewriting it with the keychain appended keeps
# every entry already there.
listed=$(security list-keychains -d user | sed 's/^[[:space:]]*"//; s/"[[:space:]]*$//')
if ! printf '%s\n' "$listed" | grep -qxF -- "$kc"; then
    oldifs=$IFS; IFS='
'
    # shellcheck disable=SC2086
    set -- $listed
    IFS=$oldifs
    security list-keychains -d user -s "$@" "$kc"
    did "added $kc to the search list"
fi

[ "$changed" -eq 1 ] || echo OK
