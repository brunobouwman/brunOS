#!/usr/bin/env bash
# Run AS BRUNO on the shared Hetzner host. Idempotent.
#
#   ssh brunoos 'bash -s' < deploy/bin/bootstrap-bruno.sh
#   # or, after the repo is on the host:
#   ssh brunoos 'bash /home/bruno/claude-second-brain/deploy/bin/bootstrap-bruno.sh'
#
# Assumes Lisa's prior host setup: apt packages, /usr/local/bin/uv, sshd
# hardening, ufw, the bruno Linux user (from seed-bruno-on-host.sh).
#
# What this script does:
#   1. Verify host basics (uv, git).
#   2. Install simonthum git-sync if missing.
#   3. Clone or fast-forward the code repo into /home/bruno/claude-second-brain.
#   4. Run setup.sh (uv sync, sqlite-vec sanity).
#   5. Symlink deploy/systemd/*.{service,timer} into /etc/systemd/system/.
#   6. systemctl daemon-reload.
#
# What this script does NOT do (left for the operator):
#   - scp .claude/.env + google_token.json (use sync-secrets.sh from Mac).
#   - Vault clone + merge-driver registration (Step 24 in Phase 9 plan).
#   - systemctl enable --now (Step 26).

set -euo pipefail
trap 'echo "FAILED at line $LINENO" >&2' ERR

REPO=/home/bruno/claude-second-brain
REMOTE=https://github.com/brunobouwman/brunOS.git

echo "==> verify host basics (Lisa already provisioned these)"
command -v /usr/local/bin/uv >/dev/null \
  || { echo "ERROR: /usr/local/bin/uv missing — Lisa's setup expected" >&2; exit 1; }
command -v git >/dev/null \
  || { echo "ERROR: git missing — Lisa's setup expected" >&2; exit 1; }

if ! command -v git-sync >/dev/null; then
  echo "==> install git-sync (simonthum) — not part of Lisa's host setup"
  sudo curl -fsSL https://raw.githubusercontent.com/simonthum/git-sync/master/git-sync \
    -o /usr/local/bin/git-sync
  sudo chmod +x /usr/local/bin/git-sync
fi

echo "==> clone or fast-forward repo"
if [[ -d "$REPO/.git" ]]; then
  git -C "$REPO" pull --ff-only
else
  git clone "$REMOTE" "$REPO"
fi

echo "==> setup.sh"
cd "$REPO"
bash setup.sh

echo "==> systemd unit symlinks"
shopt -s nullglob
for unit in "$REPO"/deploy/systemd/*.service "$REPO"/deploy/systemd/*.timer; do
  sudo ln -sf "$unit" "/etc/systemd/system/$(basename "$unit")"
done
sudo systemctl daemon-reload

echo "==> done."
echo "    Next:"
echo "      1) From Mac: deploy/bin/sync-secrets.sh   # scp .env + google_token.json"
echo "      2) On VPS:   /usr/local/bin/uv run python .claude/scripts/memory_index.py --full"
echo "      3) On VPS:   sudo systemctl enable --now \\"
echo "                     brunoosbrain-vault-sync.timer \\"
echo "                     brunoosbrain-heartbeat.timer \\"
echo "                     brunoosbrain-reflect.timer \\"
echo "                     brunoosbrain-weekly-review.timer \\"
echo "                     brunoosbrain-news-digest.timer \\"
echo "                     brunoosbrain-slackbot.service"
