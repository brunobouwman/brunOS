#!/usr/bin/env bash
# Push .claude/.env + Google OAuth token from Mac → VPS.
# This is the ONLY point in the deploy where secrets transit the wire (scp,
# encrypted). Don't commit the resulting .env anywhere.
#
# google_client_secrets.json is intentionally NOT scp'd — only the runtime
# token is needed (.claude/scripts/integrations/_google.py reads
# GOOGLE_OAUTH_TOKEN_PATH).

set -euo pipefail
trap 'echo "FAILED at line $LINENO" >&2' ERR

: "${VPS_HOST:=brunoos}"   # Mac ~/.ssh/config alias: User=bruno, HostName=49.13.165.23

REPO="$(cd "$(dirname "$0")/../.." && pwd)"

if [[ ! -f "$REPO/.claude/.env" ]]; then
  echo "ERROR: $REPO/.claude/.env not found." >&2
  exit 1
fi

ssh "$VPS_HOST" 'mkdir -p /home/bruno/claude-second-brain/.claude/data/state'

scp "$REPO/.claude/.env" \
    "$VPS_HOST:/home/bruno/claude-second-brain/.claude/.env"

if [[ -f "$REPO/.claude/data/state/google_token.json" ]]; then
  scp "$REPO/.claude/data/state/google_token.json" \
      "$VPS_HOST:/home/bruno/claude-second-brain/.claude/data/state/google_token.json"
else
  echo "WARN: $REPO/.claude/data/state/google_token.json not found — Gmail/Calendar reads will 401 on VPS until you run bootstrap_google_oauth.py and re-sync." >&2
fi

ssh "$VPS_HOST" 'chmod 600 /home/bruno/claude-second-brain/.claude/.env'
ssh "$VPS_HOST" 'test -f /home/bruno/claude-second-brain/.claude/data/state/google_token.json && chmod 600 /home/bruno/claude-second-brain/.claude/data/state/google_token.json || true'

echo "==> sync done."
echo "==> verify on VPS:"
echo "      ssh $VPS_HOST 'stat -c %a /home/bruno/claude-second-brain/.claude/.env'   # should print 600"
echo
echo "==> remember to edit on VPS .env:"
echo "      BRUNOS_VAULT_PATH=/home/bruno/BrunOS"
echo
echo "==> Anthropic auth: do NOT add ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN to .env."
echo "    Instead, install Claude Code on the VPS and \`claude login\` there:"
echo "      ssh brunoos 'which claude || curl -fsSL https://claude.ai/install.sh | bash'"
echo "      ssh -t brunoos claude login"
echo "    The Agent SDK auto-discovers the local OAuth state (auto-refresh, per-host isolation,"
echo "    bills against your Claude Max subscription). See deploy/README.md step 3a."
