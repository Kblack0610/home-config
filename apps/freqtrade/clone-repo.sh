#!/bin/sh
# Check out the strategy repo at the pinned commit into /repo (an emptyDir).
set -eu
# The emptyDir is root-owned (fsGroup sets only its group), so git as uid 1000
# calls it dubious ownership without this.
git config --global --add safe.directory /repo
git init -q /repo
git -C /repo remote add origin "$REPO_URL"
git -C /repo fetch -q --depth=1 origin "$REPO_REF"
git -C /repo checkout -q FETCH_HEAD
echo "freqtrade repo at $(git -C /repo rev-parse HEAD)"
