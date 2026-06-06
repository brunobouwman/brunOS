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

## Monitoring (Track D Phase 1, 2026-06-03)

Every service now reports through `sync_common.SyncReporter` — status file
(`.claude/data/state/<svc>-state.json`) + rate-limited Slack alert
(`BRUNOS_ALERT_CHANNEL`) + healthchecks.io dead-man's-switch with the
status.json **POSTed as the ping body** (the fleet view reads it back via the
healthchecks API). One check per service per host, named `<brain>-<svc>-<host>`.

| Service | Check env var | Cadence → grace | Notes |
|---|---|---|---|
| vault-sync | `BRUNOS_HEALTHCHECK_URL` | 2 min → 10 min | pre-existing |
| code-sync | `BRUNOS_CODESYNC_HEALTHCHECK_URL` | 30 min → 45 min | VPS-only |
| reflect | `BRUNOS_REFLECT_HEALTHCHECK_URL` | daily → 26 h | VPS-only |
| federation-doctor | `BRUNOS_FEDERATION_DOCTOR_HEALTHCHECK_URL` | daily → 26 h | VPS-only |
| heartbeat | `BRUNOS_HEARTBEAT_HEALTHCHECK_URL` | 30 min (08–22 BRT) → 45 min* | guardrail-block alerts |
| slackbot-watchdog | `BRUNOS_SLACKBOT_HEALTHCHECK_URL` | 15 min → 30 min | **stop with the bot on failover** |
| memory-doctor | `BRUNOS_MEMORY_DOCTOR_HEALTHCHECK_URL` | daily 09:15 → 26 h | index + search canary |
| inbox-rsync | `BRUNOS_INBOX_RSYNC_HEALTHCHECK_URL` | 2 min → 10 min | Mac-only |
| linos-inbox-sync | `BRUNOS_LINOS_INBOX_SYNC_HEALTHCHECK_URL` | daily ≈08:45 → 26 h | VPS-only |
| inbox-retire-vps | `BRUNOS_INBOX_RETIRE_VPS_HEALTHCHECK_URL` | daily 10:30 → 26 h | VPS-only; enable only after LinOS consumer/ack path is live |
| inbox-retire | `BRUNOS_INBOX_RETIRE_HEALTHCHECK_URL` | daily 11:30 → 26 h | Mac-only; pings in dry-run too |
| linos-consumer | `LINOS_CONSUMER_HEALTHCHECK_URL` | daily 09:00 → 26 h | LinOS node only |

\* heartbeat only runs 08:00–22:00 BRT — either schedule the check's expected
period accordingly on healthchecks.io or accept the overnight gap via a 10½ h
grace. New units to install on the VPS: `brunoosbrain-slackbot-watchdog.{service,timer}`,
`brunoosbrain-memory-doctor.{service,timer}` (symlink + `daemon-reload` +
`enable --now`, same as the others). `brunoosbrain-heartbeat.service` gained
`OnFailure=` — needs a one-time `daemon-reload`.

Manual probes: `uv run python .claude/scripts/slackbot_watchdog.py --dry-run`,
`uv run python .claude/scripts/memory_doctor.py --dry-run`.

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

## LinOS node (BaaS Track A — Phase C.5)

The LinOS node runs as user `linos` under the `linosbrain-*` systemd namespace.
It is the **read-side** of the BrunOS→LinOS federation: it integrates cleared,
in-scope captures into LinOS's own vault and publishes an ack manifest that will
eventually unlock BrunOS's F2 retirement job.

`linos` has **zero access to `/home/bruno`** (which stays `0700`). Instead, a
bruno-side push (`sync_cleared_inbox.py`, below) mirrors only the captures LinOS
is authorized to see into a LinOS-readable inbox dir; `linos_consumer.py` reads
that mirror, never BrunOS's real inbox.

### Host shape

- **User**: `linos`
- **Services**: `linosbrain-*`
- **Log dir**: `/var/log/linosbrain/`
- **Repo**: `/home/linos/claude-second-brain/` (clone of this repo — same code, different env)
- **Vault**: `/home/linos/LinOS/` (own private GitHub repo: `protostack-linos/linos-brain`)
- **Inbox mirror**: `/home/linos/brunos-inbox/sessions/` (LinOS-readable; written only by the bruno-side push — see below. Set `BRUNOS_INBOX_PATH` in LinOS's `.claude/.env` to this path.)

### Cleared-inbox transport (bruno → LinOS)

`deploy/bin/sync_cleared_inbox.py` runs **as `bruno`** (the only user that can
read `/home/bruno/BrunOS`) and rsyncs into LinOS's inbox mirror **only** the
captures that pass LinOS's federation gate — `default_export == linos-protostack`
(via `shared.validate_consumer_read`) **AND** `share_status == "cleared"`.

Both gates are required. `share_status: cleared` alone is **not** an
authorization — reflection stamps `cleared` on every capture once personal
asides are stripped, so most `default_export: personal` (Vertik/lab-agent/
chat-ui) captures are also `cleared`. On the live inbox today that is the
difference between **119** captures (cleared-only — leaks confidential work) and
**9** (scope+cleared — only LinOS-authorized). The scope check is the actual
privacy boundary, enforced at the transport so an out-of-scope capture is never
even physically present in LinOS's tree (defense-in-depth with the consumer's
own re-check).

```bash
# bruno-side, scheduled BETWEEN BrunOS reflect (≈08:00/08:30) and the consumer (09:00):
BRUNOS_INBOX_SRC=/home/bruno/BrunOS/Memory/_inbox/sessions \
LINOS_INBOX_DEST=/home/linos/brunos-inbox/sessions \
  /usr/local/bin/uv run python deploy/bin/sync_cleared_inbox.py --dry-run   # preview, then drop --dry-run
```

Safety mirrors `sync_inbox.py`: `-a --update` (never clobbers a newer dest
file), **no `--delete`**, idempotent. A not-yet-cleared capture is simply skipped
until a later run after it's cleared. The `bruno`→`linos` dir-ownership wiring
(group/ACL on the dest mirror) is settled at deploy time; the script presumes
only that `bruno` can read the src and write the dst.

### Schedule

| Timer | Runs as | Schedule | Purpose |
|-------|---------|----------|---------|
| `brunoosbrain-linos-inbox-sync.timer` | `bruno` | 08:45 BRT daily | Push cleared+in-scope captures → LinOS inbox mirror |
| `linosbrain-vault-sync.timer` | `linos` | Every 2 min | LinOS vault ↔ GitHub git-sync |
| `linosbrain-consumer.timer` | `linos` | 09:00 BRT daily | Integrate the inbox mirror into LinOS vault |
| `linosbrain-reflect.timer` | `linos` | 09:15 BRT daily | Company-brain leadership/gap reflection after consumer |
| `linosbrain-dream.timer` | `linos` | 09:25 BRT daily | Company-brain playbook proposals after reflection |
| `linosbrain-slackbot-restart.timer` | root | Sunday 04:10 BRT | Restart LinOS company chat if enabled |

Ordering matters: BrunOS reflect (08:00) stamps `cleared` → bruno-side push
(08:45) mirrors scope+cleared captures out → LinOS consumer (09:00) integrates
→ company-brain reflection/dreaming (09:15/09:25) produces reviewable
leadership, gap, and proposed playbook artifacts. The gaps absorb reflect
overruns on the shared CX21.

### Coexistence invariants

- `linos` never reads `/home/bruno`; the only data crossing is what `bruno`'s push explicitly mirrors out (scope+cleared). `linos_consumer.py` reads only `/home/linos/brunos-inbox/`.
- No cross-user `systemctl` calls: each brain manages only its own `*osbrain-*`.
- Consumer watermark state: `/home/linos/claude-second-brain/.claude/data/state/consumer_watermark.json`.
- Company-brain reflection/dreaming state: `/home/linos/claude-second-brain/.claude/data/state/company_brain_reflect_linos.json`.
- Ack manifests: `/home/linos/LinOS/Memory/_acks/brunos/<capture_id>.json`.
- Reflection artifacts: `/home/linos/LinOS/Memory/digests/leadership/<ISO-week>.md` and `/home/linos/LinOS/Memory/digests/gaps/<date>.md`.
- Dreaming artifacts: `/home/linos/LinOS/Memory/playbook/company/<date>.md` with `status: proposed`.

### First-time deploy (summary)

See `CLAUDE.md` § Phase C.5 and the plan at `.agents/plans/dt-*-baas-track-a-consumer-loop.md` for the full bootstrap sequence. Abbreviated:

```bash
# Prereqs: user linos exists, /home/linos/claude-second-brain/ is a git clone of this repo,
#           /home/linos/LinOS/ vault dir exists, .claude/.env has LinOS vars
#           (BRUNOS_INBOX_PATH=/home/linos/brunos-inbox/sessions, LINOS_VAULT_PATH=/home/linos/LinOS),
#           and the bruno-side brunoosbrain-linos-inbox-sync timer is installed (populates the mirror).

# Symlink units. Run the glob under sudo because /home/linos is private:
ssh brunoos 'sudo bash -c '"'"'for f in /home/linos/claude-second-brain/deploy/systemd/linosbrain-*.service /home/linos/claude-second-brain/deploy/systemd/linosbrain-*.timer; do
  ln -sf "$f" /etc/systemd/system/"$(basename "$f")"
done'"'"' && sudo systemctl daemon-reload'

# Log dir:
ssh brunoos 'sudo mkdir -p /var/log/linosbrain && sudo chown linos:linos /var/log/linosbrain'

# Claude auth: run once as the linos Unix user before consumer dogfood:
ssh -t brunoos 'sudo -H -u linos claude login'

# Consumer and company-brain routine dry-runs first:
ssh brunoos 'sudo -u linos /usr/local/bin/uv run python \
  /home/linos/claude-second-brain/.claude/scripts/linos_consumer.py --dry-run 2>&1'
ssh brunoos 'sudo -H -u linos bash -lc '"'"'cd /home/linos/claude-second-brain && \
  uv run python .claude/scripts/company_brain_reflect.py reflect --profile linos --dry-run && \
  uv run python .claude/scripts/company_brain_reflect.py dream --profile linos --dry-run'"'"''

# Enable vault-sync first. Enable consumer/reflect/dream only after identity + dogfood pass:
ssh brunoos 'sudo systemctl enable --now \
  linosbrain-vault-sync.timer'
```

Live dogfood state (2026-06-06): `linos` user/repo/vault/logs are provisioned,
LinOS vault sync is live, the LinOS identity seed is committed, the Bruno-side
`brunoosbrain-linos-inbox-sync.timer` is enabled, and `linosbrain-consumer.timer`
is enabled for 09:00 BRT. Consumer dogfood imported 9 eligible Colinas captures
into distinct `Memory/joint/colinas/*.md` notes and wrote 9 ack manifests.
Stage-0 Slack chat is also live behind the deterministic channel registry.
Company-brain reflection/dreaming uses the reusable
`.claude/scripts/company_brain_reflect.py` routine with
`COMPANY_BRAIN_PROFILE=linos`; `linosbrain-reflect.timer` runs at 09:15 BRT and
`linosbrain-dream.timer` runs at 09:25 BRT.

### Reusing this for client company brains

The LinOS setup is the first profile of a generic company-brain bootstrap. For
each client/company brain, create a dedicated Unix user, repo checkout, vault
repo, systemd namespace, and env:

```bash
COMPANY_BRAIN_PROFILE=<client-slug>
COMPANY_BRAIN_NAME=<Client Brain Name>
COMPANY_BRAIN_VAULT_PATH=/home/<user>/<CompanyVault>
BRUNOS_INBOX_PATH=/home/<user>/<producer-inbox-mirror>/sessions
```

The same `company_brain_reflect.py` commands write only reviewable company
artifacts: leadership digests, gap digests, and proposed playbooks. They should
run after that company's consumer/import pass, never before it.

### LinOS company chat (task 9 prep)

LinOS can reuse the Phase 7 Slack bot, but only in a LinOS-specific company
profile. Stage 0 is founder-only dogfood; the product target is a channel-scoped
company brain that can answer inside approved groups and learn from them without
requiring every individual brain to be added everywhere.

The prepared units are:

- `linosbrain-slackbot.service`
- `linosbrain-slackbot-restart.service`
- `linosbrain-slackbot-restart.timer`

The service sets `CHAT_BRAIN_PROFILE=linos`, which makes
`.claude/chat/system_prompt.py` load the company-brain files (`SOUL.md`,
`USER.md`, `LINMEMORY.md`, `STANDARDS.md`, `DECISIONS.md`, `ROUTINES.md`,
`ACCESS_POLICY.md`) instead of the BrunOS personal-memory context. It also sets
`CHAT_FLUSH_ENABLED=0`; the existing chat transcript flush writes through the
personal producer pipeline, so LinOS chat transcripts stay out of company memory
until a company-brain write contract is designed. For `CHAT_BRAIN_PROFILE=linos`,
the daemon enables deterministic channel registry checks by default
(`CHAT_CHANNEL_REGISTRY_ENABLED=1` unless explicitly disabled).

Product requirements before broad channel deployment:

- Add a channel registry to `ACCESS_POLICY.md` / `brain-config.json`: channel id,
  audience, allowed Slack user ids, persona/skill route, allowed read scopes,
  allowed write targets, ingestion mode (`ask-only`, `ingest-and-answer`,
  `digest-only`), retention, and redaction rules.
- Fail closed for unknown channels/scopes. The bot can ask for operator
  configuration, but must not infer access rights from channel names alone.
- Build a company chat-ingestion path separate from `dispatch_flush`; group
  conversation learning should write scoped company memory with provenance, not
  personal daily logs.
- Preserve brain-to-brain access: individual brains remain the worker's primary
  work surface and can query the company brain through a scoped RPC/API when
  they need company context.

For stage-0 LinOS dogfood, these conditions are already satisfied. For any new
company brain or broader channel rollout, do not enable the service until all
are true:

- A separate LinOS Slack app exists with `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`
  stored in `/home/linos/claude-second-brain/.claude/.env`.
- `Memory/Brain/brain-config.json` has the real `slack:<channel_id>` and
  `allowed_users` Slack user ids set to `status: enabled`.
- The Slack app is founder/operator-only and subscribes narrowly (`message.im`
  and optionally `app_mention`; no broad channel-message firehose).
- The runtime brain user can make Claude SDK calls without auth/rate-limit
  failures.

Smoke-test before enabling:

```bash
ssh brunoos 'sudo -H -u linos bash -lc '"'"'cd /home/linos/claude-second-brain &&
  CHAT_BRAIN_PROFILE=linos CHAT_FLUSH_ENABLED=0 /usr/local/bin/uv run python .claude/chat/bot.py --smoke-test'"'"''
```

### Verify

```bash
ssh brunoos 'systemctl list-timers linosbrain-* --no-pager'
ssh brunoos 'tail -f /var/log/linosbrain/consumer.log'
```

### F2 retirement

Each ack at `/home/linos/LinOS/Memory/_acks/brunos/<capture_id>.json` signals
to BrunOS that LinOS has processed the capture. The VPS-side F2 job
(`deploy/bin/retire_vps_inbox.py`) deletes a BrunOS inbox capture once BrunOS
has processed it (`share_status: cleared`) and any destination consumer has
acked it. For LinOS-bound captures without an ack, it falls back after 15 days
only if the ack directory exists, proving the consumer side is deployed.
`active` and `quarantined` captures are never deleted by this job.

The timer ships installed-ready as `brunoosbrain-inbox-retire-vps.{service,timer}`,
but should only be enabled after the cleared push and LinOS consumer are live:

```bash
ssh brunoos 'cd /home/bruno/claude-second-brain && /usr/local/bin/uv run python deploy/bin/retire_vps_inbox.py'
ssh brunoos 'sudo systemctl enable --now brunoosbrain-inbox-retire-vps.timer'
```

Every applied deletion is recorded in `.claude/data/state/retired_inbox.json`
and mirrored to `.claude/data/state/inbox-retired-excludes.txt` for
`sync_inbox.py`'s optional `BRUNOS_INBOX_EXCLUDE_FILE` resurrection guard. The
primary guard remains the Mac-side `retire_local_inbox.py`, which deletes the
producer copy once the VPS has the capture in a terminal state.

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
    brunoosbrain-linos-inbox-sync.{service,timer}
    linosbrain-slackbot.service
    linosbrain-slackbot-restart.{service,timer}
    linosbrain-{consumer,dream,reflect,vault-sync}.{service,timer}
    brunoosbrain-alert@.service             template — OnFailure alert (Slack via vault_sync.py --emit-alert)
  vault/
    gitignore                               template for BrunOS/.gitignore (drafts/active/, personal/finance.md, .DS_Store, .obsidian/)
    gitattributes                           template for BrunOS/.gitattributes (Memory/daily/*.md + HABITS.md → concat-both)
```
