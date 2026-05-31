#!/usr/bin/env bash
# Wire PUSH auth for a dev-task dev clone using a dedicated, least-privilege SSH
# WRITE deploy key — no tokens, nothing secret persisted beyond a standard SSH
# private key in ~/.ssh (600). FETCH stays on the existing read-only deploy key;
# only PUSH uses the new key, via a dedicated ssh-config Host alias.
#
#   ssh brunoos 'bash -s' < deploy/bin/setup-devclone-pushkey.sh
#
# After running, paste the printed PUBLIC key into:
#   github.com/<owner>/<repo> → Settings → Deploy keys → Add deploy key
#   → title "devtask-vps-push" → ✅ Allow write access → Add key
# Push then works immediately; idempotent (safe to re-run).
set -euo pipefail

DEST="${DEST:-/home/bruno/dev/brunOS}"
OWNER_REPO="${OWNER_REPO:-brunobouwman/brunOS}"
ALIAS_HOST="${ALIAS_HOST:-github-devtask-brunos}"
KEY="${KEY:-$HOME/.ssh/devtask_brunos}"
CFG="$HOME/.ssh/config"

# 1. fresh dedicated keypair (no passphrase — automation key)
if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -f "$KEY" -N "" -C "devtask-push-${OWNER_REPO}" >/dev/null
  echo "generated dedicated push key: $KEY"
else
  echo "push key already exists: $KEY"
fi
chmod 600 "$KEY"

# 2. ssh-config Host alias that uses ONLY this key for github (push side)
mkdir -p "$(dirname "$CFG")"; touch "$CFG"; chmod 600 "$CFG"
if ! grep -q "Host ${ALIAS_HOST}\$" "$CFG"; then
  printf '\nHost %s\n  HostName github.com\n  User git\n  IdentityFile %s\n  IdentitiesOnly yes\n' \
    "$ALIAS_HOST" "$KEY" >> "$CFG"
  echo "added ssh-config alias: $ALIAS_HOST"
else
  echo "ssh-config alias already present: $ALIAS_HOST"
fi

# 3. point the dev clone's PUSH url at the alias (fetch url untouched)
git -C "$DEST" remote set-url --push origin "git@${ALIAS_HOST}:${OWNER_REPO}.git"
echo "fetch url: $(git -C "$DEST" config --get remote.origin.url)"
echo "push  url: $(git -C "$DEST" config --get remote.origin.pushurl)"

echo
echo "================= ADD THIS AS A WRITE DEPLOY KEY ================="
echo "repo: github.com/${OWNER_REPO}  →  Settings → Deploy keys → Add"
echo "title: devtask-vps-push   |   ✅ Allow write access"
echo "-----------------------------------------------------------------"
cat "${KEY}.pub"
echo "================================================================="
