#!/usr/bin/env bash
# Symlink the deploy/launchd/*.plist files into ~/Library/LaunchAgents/.
# All plists ship with Disabled=true except git-sync (read consumer, dual-run
# safe). Loading is left to the operator (failover one-liner below).

set -euo pipefail
trap 'echo "FAILED at line $LINENO" >&2' ERR

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
TARGET="$HOME/Library/LaunchAgents"
mkdir -p "$TARGET"

count=0
for plist in "$REPO/deploy/launchd"/*.plist; do
  [[ -f "$plist" ]] || continue
  name="$(basename "$plist")"
  ln -sf "$plist" "$TARGET/$name"
  plutil -lint "$plist" >/dev/null
  count=$((count + 1))
done

echo "==> linked $count plists into $TARGET"
echo
echo "==> to LOAD git-sync now (safe to dual-run with VPS):"
echo "      launchctl bootstrap gui/\$(id -u) $TARGET/com.bruno.brunos.git-sync.plist"
echo
echo "==> to ENABLE another unit for FAILOVER (Mac primary):"
echo "      defaults write $TARGET/com.bruno.brunos.<svc>.plist Disabled -bool false"
echo "      launchctl bootstrap gui/\$(id -u) $TARGET/com.bruno.brunos.<svc>.plist"
echo "      # CRITICAL: stop the VPS counterpart first, e.g."
echo "      #   ssh brunoos sudo systemctl stop brunoosbrain-slackbot"
