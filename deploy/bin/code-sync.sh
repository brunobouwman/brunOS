#!/usr/bin/env bash
# VPS code-sync: pull-only, then recycle the slackbot ONLY if its in-process code
# changed. The slackbot is a long-lived daemon and doesn't hot-reload Python, so
# changes to the code it imports at startup are stale until restart. Oneshot
# timers (heartbeat/reflect/...) and the bot's subprocess helpers (query.py,
# memory_search.py, memory_flush.py) re-exec on their own — they need no restart.
#
# Runs as user `bruno` from brunoosbrain-code-sync.service. The restart goes
# through a scoped sudoers entry (deploy/sudoers/brunoosbrain-codesync); it is
# best-effort so a missing/incorrect sudoers never fails the pull.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"

before="$(git rev-parse HEAD)"
git pull --ff-only origin main
after="$(git rev-parse HEAD)"

if [[ "$before" == "$after" ]]; then
  exit 0  # nothing pulled
fi

# Paths loaded into the long-lived slackbot process at import time. session-start-
# context.py is included because chat/system_prompt.py imports it (module cached).
BOT_CODE_RE='^\.claude/chat/|^\.claude/scripts/(shared|sanitize)\.py$|^\.claude/hooks/session-start-context\.py$'

if git diff --name-only "$before" "$after" | grep -qE "$BOT_CODE_RE"; then
  echo "[code-sync] bot code changed ($before..$after) — recycling slackbot"
  if sudo -n /usr/bin/systemctl try-restart brunoosbrain-slackbot.service; then
    echo "[code-sync] slackbot recycled (try-restart)"
  else
    echo "[code-sync] WARN: slackbot recycle failed — check /etc/sudoers.d/brunoosbrain-codesync"
  fi
else
  echo "[code-sync] pulled $before..$after — no bot-code change, slackbot left running"
fi
