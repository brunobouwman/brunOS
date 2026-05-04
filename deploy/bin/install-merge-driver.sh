#!/usr/bin/env bash
# Registers the merge.concat-both driver in the *current* git repo.
# Must be run from inside the vault repo (cwd = $BRUNOS_VAULT_PATH).
# Per-clone configuration — git config is NOT committed, so re-run on every
# fresh clone (Mac + VPS).

set -euo pipefail
trap 'echo "FAILED at line $LINENO" >&2' ERR

if [[ ! -d .git ]]; then
  echo "ERROR: cwd must be a git repo (run from \$BRUNOS_VAULT_PATH)." >&2
  exit 1
fi

# Resolve the deploy/bin sibling of this script's parent (repo root).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DRIVER="$REPO_ROOT/deploy/bin/git-merge-concat"

if [[ ! -x "$DRIVER" ]]; then
  echo "ERROR: $DRIVER not executable." >&2
  exit 1
fi

git config merge.concat-both.name   "Concat both sides for append-only files"
git config merge.concat-both.driver "$DRIVER %O %A %B %P"

echo "Registered merge.concat-both driver:"
echo "  $(git config merge.concat-both.driver)"
