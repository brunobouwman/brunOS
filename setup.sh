#!/usr/bin/env bash
# Idempotent dev/VPS bootstrap. Used by Bruno's local Mac onboarding and by
# deploy/bin/bootstrap-bruno.sh on the VPS.

set -euo pipefail
trap 'echo "FAILED at line $LINENO" >&2' ERR

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "==> verify uv"
if ! command -v uv >/dev/null; then
  echo "ERROR: uv not on PATH. On VPS this should be /usr/local/bin/uv (Lisa's host setup)." >&2
  exit 1
fi
uv --version

echo "==> uv sync"
uv sync

echo "==> sanity import check"
uv run python -c "import claude_agent_sdk, fastembed, sqlite_vec; print('ok')"

echo "==> done."
