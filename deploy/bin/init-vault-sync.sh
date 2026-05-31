#!/usr/bin/env bash
# Provision the vault repo's sync invariants on THIS host. Idempotent.
# Supersedes install-merge-driver.sh — sets everything vault_sync.py needs so a
# fresh clone can never be "born broken":
#
#   1. Commit identity (host-labelled, so auto-sync commits are attributable).
#   2. The concat-both merge driver (append-only daily logs + HABITS.md).
#   3. Verifies .gitattributes is present (it's committed, but warn if not).
#
# We deliberately do NOT set simonthum's branch.main.sync / syncNewFiles —
# vault_sync.py owns the loop now and does its own `git add -A`, so those are
# obsolete (and their absence is exactly what dead-looped the old setup).
# vault_sync.py also self-heals 1+2 on every run; this script just makes the
# host correct *before* the first sync.
#
# Usage (run once per host, after cloning the vault):
#   BRUNOS_VAULT_PATH=/home/bruno/BrunOS BRUNOS_SYNC_HOST_LABEL=vps \
#     bash deploy/bin/init-vault-sync.sh
#   # path/label fall back to $BRUNOS_VAULT_PATH (or .claude/.env) and hostname.

set -euo pipefail
trap 'echo "FAILED at line $LINENO" >&2' ERR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DRIVER="$REPO_ROOT/deploy/bin/git-merge-concat"

# --- resolve vault path -----------------------------------------------------
VAULT="${BRUNOS_VAULT_PATH:-}"
if [[ -z "$VAULT" && -f "$REPO_ROOT/.claude/.env" ]]; then
  VAULT="$(grep -E '^BRUNOS_VAULT_PATH=' "$REPO_ROOT/.claude/.env" | tail -1 | cut -d= -f2- | tr -d '"'"'"'' )"
fi
VAULT="${VAULT:-$REPO_ROOT/BrunOS}"

if [[ ! -d "$VAULT/.git" ]]; then
  echo "ERROR: vault repo not found at '$VAULT' (.git missing)." >&2
  echo "       Set BRUNOS_VAULT_PATH or clone the vault first." >&2
  exit 1
fi
if [[ ! -x "$DRIVER" ]]; then
  echo "ERROR: merge driver not executable at $DRIVER" >&2
  exit 1
fi

LABEL="${BRUNOS_SYNC_HOST_LABEL:-$(hostname -s 2>/dev/null || hostname)}"

cd "$VAULT"

# --- 1. commit identity -----------------------------------------------------
if [[ -z "$(git config user.email || true)" ]]; then
  git config user.email "vault-sync+${LABEL}@brunos.local"
  git config user.name  "BrunOS sync (${LABEL})"
  echo "set commit identity for ${LABEL}"
else
  echo "commit identity already set ($(git config user.name))"
fi

# --- 2. concat-both merge driver -------------------------------------------
git config merge.concat-both.name   "Concat both sides for append-only files"
git config merge.concat-both.driver "$DRIVER %O %A %B %P"
echo "registered merge.concat-both -> $(git config merge.concat-both.driver)"

# --- 3. .gitattributes sanity ----------------------------------------------
if [[ -f .gitattributes ]] && grep -q 'merge=concat-both' .gitattributes; then
  echo ".gitattributes maps append-only files to concat-both — ok"
else
  echo "WARN: .gitattributes missing/incomplete — concat-both won't apply to daily logs" >&2
fi

echo "vault sync provisioned for host '${LABEL}' at ${VAULT}"
