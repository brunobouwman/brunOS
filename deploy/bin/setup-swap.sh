#!/usr/bin/env bash
# Idempotent 2GB swapfile setup for the shared CX21 (2 vCPU / 4 GB, no swap by
# default). The Slack bot's per-thread SDK subprocesses spike memory; swap turns
# a transient spike into slowdown instead of a silent Socket-Mode death / OOM.
#
# Run on the VPS as root:  sudo bash deploy/bin/setup-swap.sh
# Coexistence: this is host-wide and benefits Lisa's services too — harmless.
set -euo pipefail

SWAPFILE=/swapfile
SIZE_MB=2048

if swapon --show --noheadings | grep -q .; then
  echo "swap already active:"
  swapon --show
  exit 0
fi

if [[ -f "$SWAPFILE" ]]; then
  echo "$SWAPFILE exists but is not active; activating."
else
  echo "creating ${SIZE_MB}MB swapfile at $SWAPFILE"
  # fallocate can produce a file swapon rejects on some FSes; dd is portable.
  dd if=/dev/zero of="$SWAPFILE" bs=1M count="$SIZE_MB" status=progress
  chmod 600 "$SWAPFILE"
  mkswap "$SWAPFILE"
fi

swapon "$SWAPFILE"

if ! grep -qE "^\s*$SWAPFILE\s" /etc/fstab; then
  echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab
  echo "added $SWAPFILE to /etc/fstab (persists across reboot)"
fi

# Light swappiness: only lean on swap under real pressure, not proactively.
sysctl -w vm.swappiness=10
if ! grep -qE "^\s*vm.swappiness" /etc/sysctl.conf; then
  echo "vm.swappiness=10" >> /etc/sysctl.conf
fi

echo "done:"
swapon --show
free -h
