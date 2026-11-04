#!/bin/sh
# Check out the strategy repo at the pinned commit into /repo (an emptyDir).
set -eu
git init -q /repo
git -C /repo remote add origin "$REPO_URL"
git -C /repo fetch -q --depth=1 origin "$REPO_REF"
git -C /repo checkout -q FETCH_HEAD
echo "freqtrade repo at $(git -C /repo rev-parse HEAD)"
