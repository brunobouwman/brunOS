#!/usr/bin/env bash
# Run from Mac AS ROOT on the shared Hetzner host (49.13.165.23) to seed
# Bruno's SSH pubkey into /home/bruno/.ssh/authorized_keys and grant NOPASSWD
# sudo. Idempotent — re-runs are no-ops.
#
#   ./deploy/bin/seed-bruno-on-host.sh
#   ./deploy/bin/seed-bruno-on-host.sh ~/.ssh/id_ed25519.pub
#
# Lisa's host bootstrap may already have created the bruno user; this script
# guards every step so a second run is harmless.

set -euo pipefail
trap 'echo "FAILED at line $LINENO" >&2' ERR

KEYFILE="${1:-$HOME/.ssh/id_ed25519.pub}"
PUBKEY="$(cat "$KEYFILE")"
: "${VPS_HOST:=root@49.13.165.23}"

ssh "$VPS_HOST" "PUBKEY='$PUBKEY' bash -s" <<'REMOTE'
set -euo pipefail
id bruno >/dev/null 2>&1 || adduser --disabled-password --gecos '' bruno
install -d -m 700 -o bruno -g bruno /home/bruno/.ssh
touch /home/bruno/.ssh/authorized_keys
grep -qxF "$PUBKEY" /home/bruno/.ssh/authorized_keys \
  || echo "$PUBKEY" >> /home/bruno/.ssh/authorized_keys
chmod 600 /home/bruno/.ssh/authorized_keys
chown bruno:bruno /home/bruno/.ssh/authorized_keys
if ! grep -q '^bruno ALL=' /etc/sudoers.d/bruno 2>/dev/null; then
  echo 'bruno ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/bruno
  chmod 440 /etc/sudoers.d/bruno
fi
echo "==> bruno user seeded; sudo NOPASSWD set"
REMOTE
