# BrunOS Deploy — Phase 9

Operator runbook for the two-host deployment.

## Host shape

- **VPS (primary)**: Hetzner CX21, Ubuntu 24.04 LTS ARM64, `49.13.165.23`. **Shared with Lisa.**
  - Bruno's namespace: user `bruno`, services `brunoosbrain-*`, log dir `/var/log/brunoosbrain/`, repo at `/home/bruno/claude-second-brain`, vault at `/home/bruno/BrunOS`.
  - Lisa's namespace: user `lisa`, services `lisaosbrain-*`. Don't touch.
  - `uv` is system-wide at `/usr/local/bin/uv` (Lisa's bootstrap).
- **Mac (failover)**: All `com.bruno.brunos.*` launchd plists installed but `Disabled=true` except `git-sync` (read consumer, dual-run safe).
- **Storage**: SQLite + sqlite-vec on both hosts. The DB file (`.claude/data/state/memory.db`) is per-host — each side rebuilds its own index on first run from the synced vault.

## First-time deploy

1. **Seed Bruno's SSH key on the host** (one-time, root SSH from Mac):
   ```bash
   deploy/bin/seed-bruno-on-host.sh
   ```
   Add to `~/.ssh/config` on Mac:
   ```
   Host brunoos
     HostName 49.13.165.23
     User bruno
     IdentityFile ~/.ssh/id_ed25519
   ```
   Verify: `ssh brunoos id` returns `uid=… bruno …`.

2. **Bruno-user bootstrap on host**:
   ```bash
   ssh brunoos 'bash -s' < deploy/bin/bootstrap-bruno.sh
   ```
   Clones the code repo into `/home/bruno/claude-second-brain`, runs `setup.sh`, symlinks all `brunoosbrain-*.{service,timer}` into `/etc/systemd/system/`, installs `git-sync` if missing. Idempotent.

3. **Sync secrets**:
   ```bash
   deploy/bin/sync-secrets.sh
   ```
   Pushes `.claude/.env` + `google_token.json` (only the runtime token, not the client secrets). Then `ssh brunoos vim /home/bruno/claude-second-brain/.claude/.env` and set:
   - `BRUNOS_VAULT_PATH=/home/bruno/BrunOS`
   - **Anthropic auth**: do **not** set `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`. Instead, install Claude Code on the VPS and `claude login` once — the Agent SDK auto-discovers the local OAuth state (auto-refreshing tokens, per-host isolation, bills against your Max subscription). See step 3a below.

3a. **Install Claude Code on VPS + login** (one-time, replaces the env-var auth path):
   ```bash
   # Check if Lisa already installed it system-wide:
   ssh brunoos 'which claude || echo NOT_INSTALLED'

   # If NOT_INSTALLED, install (Anthropic's official installer for Linux ARM64):
   ssh brunoos 'curl -fsSL https://claude.ai/install.sh | bash'

   # Login (device-code flow — prints a URL + code; open URL on Mac, paste code):
   ssh -t brunoos claude login

   # Smoke-test the SDK can auto-discover the OAuth state:
   ssh brunoos 'cd /home/bruno/claude-second-brain && /usr/local/bin/uv run python .claude/chat/bot.py --smoke-test'
   ```

4. **Vault git-init** (Mac side, then VPS clone):
   ```bash
   cd "$BRUNOS_VAULT_PATH"
   git init && git branch -m main
   cp ../deploy/vault/gitignore   .gitignore
   cp ../deploy/vault/gitattributes .gitattributes
   git add -A && git commit -m "init: vault repo"
   gh repo create brunobouwman/brunOS-Vault --private --source=. --remote=origin --push
   ../deploy/bin/install-merge-driver.sh

   # On VPS — needs a deploy key for the private repo:
   ssh brunoos 'ssh-keygen -t ed25519 -N "" -f ~/.ssh/brunos-vault-deploy'
   ssh brunoos 'cat ~/.ssh/brunos-vault-deploy.pub'
   # Add the printed key to GitHub repo Settings → Deploy keys (read+write).
   # Then on VPS configure ~/.ssh/config:
   #   Host github.com
   #     IdentityFile ~/.ssh/brunos-vault-deploy
   ssh brunoos 'cd /home/bruno && git clone git@github.com:brunobouwman/brunOS-Vault.git BrunOS'
   ssh brunoos 'cd /home/bruno/BrunOS && /home/bruno/claude-second-brain/deploy/bin/install-merge-driver.sh'
   ```

5. **Cold-build the index on VPS**:
   ```bash
   ssh brunoos 'cd /home/bruno/claude-second-brain && /usr/local/bin/uv run python .claude/scripts/memory_index.py --full'
   ```

6. **Enable VPS units**:
   ```bash
   ssh brunoos 'sudo systemctl enable --now \
     brunoosbrain-vault-sync.timer \
     brunoosbrain-heartbeat.timer \
     brunoosbrain-reflect.timer \
     brunoosbrain-weekly-review.timer \
     brunoosbrain-news-digest.timer \
     brunoosbrain-slackbot.service'
   ssh brunoos 'systemctl list-timers brunoosbrain-*'
   ssh brunoos 'systemctl status brunoosbrain-slackbot --no-pager'
   ssh brunoos 'tail -50 /var/log/brunoosbrain/slackbot.log'
   ```

7. **Install Mac plists (failover-ready)**:
   ```bash
   bash deploy/bin/install-mac-launchd.sh
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bruno.brunos.git-sync.plist
   ```
   `git-sync` loads enabled. The other 5 plists are linked in `~/Library/LaunchAgents/` but stay `Disabled=true`.

## Failover (Mac primary, VPS off)

```bash
# 1. Stop the VPS counterpart FIRST — running both at once causes duplicate Slack replies.
ssh brunoos 'sudo systemctl stop brunoosbrain-slackbot brunoosbrain-heartbeat.timer brunoosbrain-reflect.timer brunoosbrain-weekly-review.timer brunoosbrain-news-digest.timer'

# 2. Flip Disabled and bootstrap each Mac unit:
for svc in heartbeat reflection weekly-review news-digest chat; do
  defaults write ~/Library/LaunchAgents/com.bruno.brunos.$svc.plist Disabled -bool false
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bruno.brunos.$svc.plist
done
```

Reverse to fail back: `launchctl bootout` each Mac unit, set `Disabled=true`, then re-enable VPS units.

## Single-instance verification

The Slack chat bot is **mandatory single-instance** — Slack Socket Mode is fan-out broadcast, so duplicate clients post duplicate replies.

```bash
# DM the bot from a phone with Mac asleep / failover not active.
# Count replies in-thread; expected = 1.
```

If you ever see two replies, check both hosts:
```bash
ssh brunoos 'systemctl is-active brunoosbrain-slackbot'    # should be active when VPS is primary
launchctl print gui/$(id -u)/com.bruno.brunos.chat 2>/dev/null \
  | grep -E "state|disabled"                               # should be NOT loaded when VPS is primary
```

The four scheduled jobs (heartbeat / reflect / weekly-review / news-digest) are **not** safe to dual-run: concat-both protects daily logs, but `MEMORY.md` and `HABITS.md` writes can race. Treat dual-running as failover only.

## Logs

| Where | Path | View |
|-------|------|------|
| VPS file form | `/var/log/brunoosbrain/<svc>.log` | `tail -f /var/log/brunoosbrain/<svc>.log` |
| VPS journal | systemd journal | `journalctl -u brunoosbrain-<svc> -f` |
| Mac | `~/Library/Logs/com.bruno.brunos.<svc>.log` | `tail -f ~/Library/Logs/com.bruno.brunos.<svc>.log` |

## Vault sync (`vault_sync.py`)

The vault sync is owned by `.claude/scripts/vault_sync.py` (NOT simonthum git-sync — that dead-looped on conflicts and depended on per-clone `syncNewFiles` config that drifted, silently freezing the vault for days). Both hosts run it every 2 min (VPS: `brunoosbrain-vault-sync.{service,timer}`; Mac: `com.bruno.brunos.git-sync.plist`, via `uv` for TCC/FDA).

Each run: `preflight` self-heals config (commit identity + concat-both driver) → `fetch` → commit any local changes (`git add -A`, so new files just work) → `merge` origin (ort + rename detection) → `push` (with one refetch+remerge+retry on a two-host race).

**Conflict policy — never leaves a broken tree.** Append-only files (`Memory/daily/*.md`, `Memory/HABITS.md`) auto-merge via the concat-both driver. Any *other* conflict (e.g. `MEMORY.md`, project notes) is **not** guessed at: the merge is `--abort`ed (working tree returns to clean + usable), a loud alert fires, and the next tick retries. Sync pauses, nothing bricks or is lost. Resolve manually (or ask the agent), then it converges.

**Observability — 3 layers + 1 backstop.** (1) In-script **Slack alert** to `BRUNOS_ALERT_CHANNEL` on failure, rate-limited (first failure / changed error / hourly). (2) systemd **`OnFailure=brunoosbrain-alert@%n.service`** catches crashes too hard to self-alert. (3) **healthchecks.io** dead-man's-switch (`BRUNOS_HEALTHCHECK_URL`, one check per host) — pinged every run; if pings stop entirely (timer/host dead) it alerts after the grace window. (4) `heartbeat.py` also echoes a stale/failing sync in its tick. Status is written to `.claude/data/state/vault-sync-state.json`.

**Provisioning.** Run `deploy/bin/init-vault-sync.sh` once per fresh vault clone (sets identity + concat-both driver). `preflight` re-asserts these every run, so a host can't be born broken. Set `BRUNOS_ALERT_CHANNEL` + `BRUNOS_HEALTHCHECK_URL` (+ optional `BRUNOS_SYNC_HOST_LABEL`) in each host's `.claude/.env`.

### Concat-both merge driver caveats

`deploy/bin/git-merge-concat` is registered for `Memory/daily/*.md` and `Memory/HABITS.md` only. It concatenates the union of unique lines from both sides, so neither Mac nor VPS appends are lost — but **line order is not preserved**. Bruno reads daily logs by timestamp anyway, so reorder is fine for these files. Don't extend the driver to non-append-only files.

For any other vault file (SOUL.md, USER.md, MEMORY.md, project notes) the sync's conflict policy applies (abort-clean + alert + retry; see above) — there are no lingering `<<<<<<<` markers in the working tree.

## Snapshot cold-start on failover

`heartbeat.py` persists state at `.claude/data/state/heartbeat-state.json`. This file is **not** in the vault git repo (lives in the code repo's `.claude/data/state/`, which is gitignored). On failover the new host has no prior snapshot, so the first tick treats everything as new and may produce a noisy first-run delta. One-time cost; ignore.

The same applies to the SQLite memory index — each host rebuilds its own from the synced vault.

## OAuth refresh-token expiry

If the Google consent screen is in **Testing** mode, refresh tokens expire after 7 days. Symptom: heartbeat logs show `auth failure` from Gmail / Calendar reads. Re-run on Mac:
```bash
uv run python .claude/scripts/bootstrap_google_oauth.py
deploy/bin/sync-secrets.sh
```
Long-term fix: switch the consent screen to **In Production / Self-Published**.

## Coexistence checklist with Lisa

- **Never** `systemctl stop lisaosbrain-*`.
- **Never** edit anything under `/home/lisa/`.
- If shared CX21 memory pressure shows up at simultaneous :00/:30 ticks, edit `brunoosbrain-heartbeat.timer` to stagger:
  ```
  OnCalendar=*-*-* 08..22:15/30 America/Sao_Paulo
  ```

## Quick smoke tests

```bash
# Bot alive (no SDK call):
ssh brunoos 'cd /home/bruno/claude-second-brain && /usr/local/bin/uv run python .claude/chat/bot.py --smoke-test'

# Heartbeat plumbing (no agent calls):
ssh brunoos 'cd /home/bruno/claude-second-brain && /usr/local/bin/uv run python .claude/scripts/heartbeat.py --dry-run --no-agent'

# Coexistence:
ssh brunoos 'systemctl --no-pager list-units --type=service --state=active | grep -E "(brunos|lisas)osbrain"'
```

## File map

```
deploy/
  README.md                                 (you are here)
  bin/
    git-merge-concat                        merge driver for daily logs + HABITS.md
    init-vault-sync.sh                      provision vault sync invariants per-clone (identity + concat-both driver)
    install-merge-driver.sh                 (legacy) register just the driver; superseded by init-vault-sync.sh
    git_sync.py                             DEPRECATED shim — delegates to .claude/scripts/vault_sync.py
    seed-bruno-on-host.sh                   one-shot, root SSH, adds Bruno's pubkey to /home/bruno/.ssh/authorized_keys
    bootstrap-bruno.sh                      run as bruno on VPS — clone repo, setup.sh, symlink units
    sync-secrets.sh                         scp .claude/.env + google_token.json to VPS
    install-mac-launchd.sh                  symlink launchd plists into ~/Library/LaunchAgents (Disabled=true except git-sync)
  launchd/
    com.bruno.brunos.heartbeat.plist
    com.bruno.brunos.reflection.plist
    com.bruno.brunos.weekly-review.plist
    com.bruno.brunos.news-digest.plist
    com.bruno.brunos.chat.plist
    com.bruno.brunos.git-sync.plist
  systemd/
    brunoosbrain-heartbeat.{service,timer}
    brunoosbrain-reflect.{service,timer}
    brunoosbrain-weekly-review.{service,timer}
    brunoosbrain-news-digest.{service,timer}
    brunoosbrain-slackbot.service
    brunoosbrain-vault-sync.{service,timer}
    brunoosbrain-alert@.service             template — OnFailure alert (Slack via vault_sync.py --emit-alert)
  vault/
    gitignore                               template for BrunOS/.gitignore (drafts/active/, personal/finance.md, .DS_Store, .obsidian/)
    gitattributes                           template for BrunOS/.gitattributes (Memory/daily/*.md + HABITS.md → concat-both)
```
