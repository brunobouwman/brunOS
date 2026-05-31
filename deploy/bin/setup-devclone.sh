#!/usr/bin/env bash
# Provision a SEPARATE dev clone of a repo on the VPS for the dev-task skill.
#
#   ssh brunoos 'bash -s' < deploy/bin/setup-devclone.sh
#
# Why a separate clone: dev-task HARD-REFUSES the live code-sync checkout
# (/home/bruno/claude-second-brain) — a branch there breaks code-sync's
# --ff-only. This clone is a sibling scratch workspace, downstream of GitHub
# main, that re-fetches origin/main every run (see dev_task.create_worktree).
#
# Auth: FETCH uses the existing read-only SSH deploy key (inherited from the
# clone). PUSH is wired SEPARATELY + securely by setup-devclone-pushkey.sh
# (a dedicated least-privilege SSH WRITE deploy key — no tokens on the box).
# Idempotent.
set -euo pipefail

REPO_SSH="${REPO_SSH:-git@github.com:brunobouwman/brunOS.git}"
DEST="${DEST:-/home/bruno/dev/brunOS}"
ALIAS="${ALIAS:-brunos-dev}"
LIVE="${LIVE:-$HOME/claude-second-brain}"
REGISTRY="$LIVE/.claude/data/state/dev-task/repos.json"

echo "==> dev clone: $REPO_SSH -> $DEST (alias '$ALIAS')"
mkdir -p "$(dirname "$DEST")"
if [ ! -d "$DEST/.git" ]; then
  git clone "$REPO_SSH" "$DEST"
else
  echo "    (already cloned)"
fi
git -C "$DEST" fetch origin --quiet || true
echo "    HEAD: $(git -C "$DEST" log --oneline -1)"

echo "==> registering alias in $REGISTRY"
mkdir -p "$(dirname "$REGISTRY")"
python3 - "$REGISTRY" "$ALIAS" "$DEST" <<'PY'
import json, os, sys
reg_path, alias, dest = sys.argv[1], sys.argv[2], sys.argv[3]
reg = {}
if os.path.exists(reg_path):
    try:
        reg = json.load(open(reg_path))
    except Exception:
        reg = {}
reg[alias] = dest
json.dump(reg, open(reg_path, "w"), indent=2)
print("    registry:", json.dumps(reg))
PY

echo "==> NEXT: wire push auth (SSH write deploy key):"
echo "    ssh brunoos 'bash -s' < deploy/bin/setup-devclone-pushkey.sh"
echo "    then add the printed PUBLIC key as a WRITE deploy key on the repo."
echo "==> done."
