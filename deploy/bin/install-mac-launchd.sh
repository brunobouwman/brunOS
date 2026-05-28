#!/usr/bin/env bash
# Install the deploy/launchd/*.plist files into ~/Library/LaunchAgents/ as REAL
# FILE COPIES (not symlinks).
#
# Why copies, not symlinks: launchd's automatic login-time scan of
# ~/Library/LaunchAgents does NOT reliably auto-load symlinked plists — a manual
# `launchctl bootstrap` of a symlink works, but it does not survive logout/reboot,
# so always-on units (git-sync, inbox-rsync) silently vanish after every login.
# Real files auto-load at login the way codex-watcher always has. Copies also keep
# `defaults write <plist> Disabled` (the failover step below) from dirtying the
# repo. Trade-off: editing a repo plist requires re-running this installer to
# propagate — acceptable for stable infra. Re-running is idempotent.
#
# Units ship Disabled=true EXCEPT git-sync + inbox-rsync (both dual-run safe). This
# script auto-bootstraps the Disabled=false units so a fresh install is running
# immediately AND after reboot; Disabled=true units are left for the operator to
# enable on failover (one-liner below).

set -euo pipefail
trap 'echo "FAILED at line $LINENO" >&2' ERR

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
TARGET="$HOME/Library/LaunchAgents"
GUI="gui/$(id -u)"
mkdir -p "$TARGET"

count=0
loaded=0
for plist in "$REPO/deploy/launchd"/*.plist; do
  [[ -f "$plist" ]] || continue
  name="$(basename "$plist")"
  label="${name%.plist}"
  dest="$TARGET/$name"

  plutil -lint "$plist" >/dev/null

  # rm first: `cp` over an existing symlink writes THROUGH it into the repo file.
  rm -f "$dest"
  cp "$plist" "$dest"
  count=$((count + 1))

  # Auto-load units that ship enabled (Disabled=false); leave failover units off.
  disabled="$(plutil -extract Disabled raw -o - "$plist" 2>/dev/null || echo true)"
  if [[ "$disabled" == "false" ]]; then
    launchctl bootout "$GUI/$label" 2>/dev/null || true
    launchctl bootstrap "$GUI" "$dest"
    loaded=$((loaded + 1))
    echo "==> bootstrapped $label (enabled)"
  fi
done

echo "==> installed $count plists into $TARGET ($loaded auto-loaded)"
echo
echo "==> to ENABLE another unit for FAILOVER (Mac primary):"
echo "      defaults write $TARGET/com.bruno.brunos.<svc>.plist Disabled -bool false"
echo "      launchctl bootstrap $GUI $TARGET/com.bruno.brunos.<svc>.plist"
echo "      # CRITICAL: stop the VPS counterpart first, e.g."
echo "      #   ssh brunoos sudo systemctl stop brunoosbrain-slackbot"
