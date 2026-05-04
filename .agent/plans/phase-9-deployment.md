# Feature: Phase 9 — Deployment (Hetzner CX21 shared host + brunoosbrain systemd + Mac launchd failover + vault git-sync)

The following plan is meant to be executed in **one pass** by a focused agent. It is comprehensive, but you must still validate codebase patterns and external docs before each step. Pay special attention to existing util/type/model names — `db.py`'s public API in particular is contractual: `memory_index.py`, `memory_search.py`, `memory_reflect.py`, and `news-digest`'s dedupe call all import from it, so any rename breaks them.

> **2026-05-04 update (this revision)**: VPS provider switched to **Hetzner CX21** (49.13.165.23, Ubuntu 24.04 LTS ARM64) and the host is **shared with Lisa**, who has already provisioned host-level dependencies and validated her own `lisaosbrain-*` services on it. Bruno's services run as a parallel namespace `brunoosbrain-*` under the `bruno` Linux user with their own Postgres role+DB and log files. The vast majority of the from-scratch VPS bootstrap (apt packages, Postgres install, uv install, sshd hardening, ufw) is **already done** and not part of Bruno's work; the new bootstrap script is a much shorter "register Bruno on a shared, working host" delta. Old DigitalOcean / `brunos-*` / `/home/bruno/brunos` references have been replaced throughout.

## Feature Description

Cut over from "Mac-only, manual heartbeat" to a production-grade two-host deployment: a Hetzner CX21 droplet (Ubuntu 24.04 LTS ARM64, 49.13.165.23) — already running Lisa's BrunOS-equivalent (`lisaosbrain-*` services) — hosts Bruno's always-on services (heartbeat, reflection, weekly review, news digest, Slack chat bot, vault git-sync) under a parallel `brunoosbrain-*` namespace. The Mac keeps installed-but-disabled launchd plists for one-command failover. The vault becomes its own git repo (private GitHub) with a custom `concat-both` merge driver so daily logs survive bidirectional sync. The vector index gains a Postgres+pgvector backend so the VPS doesn't ship sqlite-vec (and the SQLite Mac path stays unchanged for local dev).

## User Story

As **Bruno**
I want **BrunOS running 24/7 on a shared Hetzner host alongside Lisa's instance, with reliable vault sync between VPS and my Mac**
So that **the heartbeat keeps drafting replies while my laptop is asleep, the chat bot stays online for Slack, daily logs don't corrupt on merge, and I can fail over to local Mac in one command if the VPS is down — without disrupting Lisa's parallel deployment on the same host**.

## Problem Statement

Today every BrunOS service is a manual `uv run python …` invocation on the Mac. The chat bot dies when the laptop closes; the heartbeat never fires unless Bruno triggers it; reflections, weekly reviews, and news digests are entirely manual. There's no DB backend portable to a Linux VPS (sqlite-vec is fine on Mac but Postgres+pgvector is the durable choice for a multi-tenant shared deploy where Lisa already runs on the same Postgres instance). The vault has no git history, so cross-machine sync is impossible.

## Solution Statement

Ship a `deploy/` directory containing launchd plists, `brunoosbrain-*` systemd units mirroring Lisa's `lisaosbrain-*` template, a `git-merge-concat` driver, an idempotent **Bruno-side** bootstrap script (host-level provisioning is already done), Postgres init SQL for Bruno's role+DB, and a small `setup.sh` that wraps `uv sync --extra vps`. Implement a Postgres branch in `db.py` that mirrors the SQLite public API exactly (so the seven scripts that import it stay untouched). Init the vault as a git repo on Mac, push to a private GitHub repo, clone on VPS, register the merge driver per machine, and schedule git-sync every 2 minutes. VPS is primary; Mac plists install with `Disabled=true` for failover. Single-instance is **mandatory** for the chat bot (Slack Socket Mode is fan-out broadcast — duplicate clients = duplicate replies) and **strongly recommended** for the four scheduled jobs (concat-both makes dual-run safe but doubles SDK cost and risks the local-only `file_lock` race on HABITS.md / MEMORY.md).

## Feature Metadata

**Feature Type**: New Capability (deployment infrastructure on a shared, partially-provisioned host)
**Estimated Complexity**: Medium-High (Postgres backend port + dual-host orchestration are non-trivial; VPS provisioning is reduced to ~1/4 of the original scope thanks to Lisa's prior work)
**Primary Systems Affected**: `db.py` (Postgres branch), new `deploy/` tree, new `setup.sh` at repo root, new vault `.gitignore`/`.gitattributes`, `pyproject.toml` (vps extra), `CLAUDE.md` (Phase 9 section)
**Dependencies**: Hetzner CX21 ARM64 (49.13.165.23) — **already provisioned**, private GitHub repo `brunobouwman/brunos-vault` (TBD by Bruno), `psycopg[binary]>=3.2`, `pgvector>=0.3` (Python adapter — needs adding to `pyproject.toml [vps]`), Postgres 16 + pgvector extension (already installed on host), [git-sync](https://github.com/simonthum/git-sync) script (install on both machines).

**Decisions confirmed by Bruno (2026-05-04)**:
- VPS: **Hetzner CX21 ARM64**, IP `49.13.165.23`, Ubuntu 24.04 LTS — **already created and SSH-key-seeded for Bruno's user-creation step**.
- Host model: **Shared with Lisa**. Lisa's `lisaosbrain-*` systemd units exist and are validated. Bruno's units mirror them exactly — same structure, just `lisa → bruno` and `lisaosbrain → brunoosbrain` throughout.
- Already-provisioned on host (Lisa did this, do **not** redo): `apt update && upgrade`, `apt install git python3 postgresql build-essential`, `uv` at `/usr/local/bin/uv` (system-wide, not per-user), sshd hardening, ufw, the `lisa` Linux user + Lisa's SSH key, PostgreSQL service running.
- Repo path on VPS: `/home/bruno/claude-second-brain` (was `/home/bruno/brunos` in older draft). Reflects the actual GitHub repo name.
- Service prefix: `brunoosbrain-*` (was `brunos-*`). Mirrors Lisa's `lisaosbrain-*` namespace on the same host.
- Postgres role + DB: both named `brunoosbrain` (Lisa uses `lisaosbrain`).
- `uv` invocation in systemd `ExecStart`: **`/usr/local/bin/uv`** (system-wide, not `/home/bruno/.local/bin/uv`).
- Vault remote: private GitHub repo `brunobouwman/brunos-vault`.
- Phase 8 (security hardening): **already merged** (commits 97f8a07, b08b993, 2026-05-03). Phase 9 must NOT re-edit `.claude/scripts/sanitize.py`, `.claude/hooks/dangerous-bash.py`, `.claude/hooks/block-secrets.py`, or the `PreToolUse` block in `.claude/settings.json` — they are already in place.

**Still Bruno's job (delta from Lisa's prior work)**:
1. Get Bruno's SSH public key into `/home/bruno/.ssh/authorized_keys` (one-time, run as root from Mac — see Step 1 below).
2. Author `setup.sh` (idempotent venv bootstrap, lives at repo root, runs `uv sync --extra vps`).
3. Clone the code repo on VPS, run `setup.sh`.
4. Author + scp `.claude/.env` with all integration tokens.
5. scp Google OAuth token from Mac (`google_token.json`).
6. Init Bruno's Postgres role + DB (separate from Lisa's; same Postgres instance).
7. Vault git-init on Mac, push to GitHub, clone on VPS, register concat-both merge driver per machine.
8. Symlink `brunoosbrain-*` unit files into `/etc/systemd/system/`, enable + start.
9. Mac launchd plists installed with `Disabled=true` for failover.
10. `db.py` Postgres branch (host-independent code change — same as before; backend dispatched via `DB_BACKEND` env var).

---

## CONTEXT REFERENCES

### Relevant Codebase Files — YOU MUST READ THESE BEFORE IMPLEMENTING

- `.claude/scripts/db.py` (entire file, 178 lines) — Why: defines the **contractual public API** the Postgres branch must mirror exactly: `connect()`, `init_schema(conn)`, `upsert_chunk(conn, file_path, chunk_idx, content, mtime, embedding) -> int`, `delete_chunks_for_file(conn, file_path) -> int`, `vector_search(conn, qemb, k, path_prefix=None) -> list[dict]`, `keyword_search(conn, query, k, path_prefix=None) -> list[dict]`, `all_file_mtimes(conn) -> dict[str, float]`, `get_chunks(conn, ids) -> dict[int, dict]`. Module constant `EMBED_DIM = 384`. The current Postgres branch in `connect()` raises `NotImplementedError(f"DB_BACKEND={backend} ships in Phase 9")` (line 56) — replace it. Returned row dicts must contain the exact same keys the SQLite path returns (`id, file_path, chunk_idx, content, distance` for vector; `id, file_path, chunk_idx, content, score` for FTS).
- `.claude/scripts/memory_search.py` (entire file, 60 lines) — Why: shows how RRF fusion consumes `vector_search` and `keyword_search`. Only ordinal rank matters (`rank` index in the loop, not the score value itself), so Postgres' `ts_rank_cd` (higher = better) and SQLite's `bm25` (lower = better) both work as long as each backend returns rows already sorted best-first.
- `.claude/scripts/memory_index.py` lines 1-80 — Why: imports `all_file_mtimes`, `connect`, `delete_chunks_for_file`, `init_schema`, `upsert_chunk` from `db`. Confirms the `EXCLUDE_RELATIVE = {"personal/finance.md"}` boundary still applies on Postgres path.
- `.claude/scripts/integrations/_google.py` (entire file, 69 lines) — Why: confirms the runtime token path is `GOOGLE_OAUTH_TOKEN_PATH` env (default `.claude/data/state/google_token.json`). Only the token is needed at runtime; `google_client_secrets.json` is read by `bootstrap_google_oauth.py` once on Mac and is **not** required on VPS.
- `.claude/scripts/shared.py` lines 20-87, 240-258 — Why: `REPO_ROOT`, `STATE_DIR`, `BRT` constants you'll reference in deploy artifacts; `load_env()` and `vault_path()` are how every script picks up env vars (the `.env` path is `REPO_ROOT/.claude/.env`); `_resolve_uv()` shows the uv-binary discovery pattern your systemd `ExecStart` uses (`/usr/local/bin/uv run …` on the shared Hetzner host).
- `.claude/chat/bot.py` lines 1-60 — Why: `CLAUDE_INVOKED_BY=chat` is set BEFORE any SDK import. Your `brunoosbrain-slackbot.service` ExecStart is just `uv run python .claude/chat/bot.py`; the script handles its own env. **Single-instance enforcement target** — see PRD §9.5.
- `.claude/scripts/heartbeat.py` — Why: confirms `_split_chat_bot_handled()` queries Slack API directly, so it works regardless of which machine the bot runs on. Schedule: every 30 min 08:00–22:00 BRT (your `OnCalendar` expression must hit those exact slots).
- `pyproject.toml` lines 30-32 — Why: `[project.optional-dependencies] vps = ["psycopg[binary]>=3.2,<4"]` already exists; you'll add `"pgvector>=0.3,<0.4"` to it.
- `.gitignore` lines 23-27 — Why: `BrunOS/` is gitignored from the code repo, by design. The Phase 9 vault git-init runs **inside** `BrunOS/` and writes a separate `.gitignore` and `.gitattributes` there.
- `.claude/.env.example` (entire file — protected by `block-secrets.py` hook; read it with care, e.g. `git show HEAD:.claude/.env.example`) — Why: documents every env var; `BRUNOS_VAULT_PATH`, `DB_BACKEND`, `POSTGRES_URL`, `ANTHROPIC_API_KEY` (commented note), Google OAuth paths.
- `.agent/plans/second-brain-prd.md` lines 585-686 — Why: canonical PRD for Phase 9. Read before implementing — especially §9.5 single-instance rationale and §9.4 git-sync setup.
- **Lisa's reference templates on the VPS** — Why: copy-paste the structural shell (after `lisa → bruno` / `lisaosbrain → brunoosbrain` substitution). Locations on the host (read-only via `ssh root@49.13.165.23 cat ...`):
  - `/etc/systemd/system/lisaosbrain-*.{service,timer}` — every unit Bruno mirrors.
  - `/home/lisa/claude-second-brain/setup.sh` (or equivalent) — Bruno's `setup.sh` should match shape.
  - `/var/log/lisaosbrain-*.log` and the `LogsDirectory=lisaosbrain` systemd directive — log layout to mirror.

### New Files to Create

```
setup.sh                                   # NEW (repo root) — idempotent venv bootstrap; called by VPS install + dev onboarding

deploy/
  README.md                                # operator runbook (Hetzner CX21 + shared-host caveats)
  bin/
    git-merge-concat                       # merge driver (executable)
    install-merge-driver.sh                # registers merge.concat-both per machine
    seed-bruno-on-host.sh                  # one-shot, run AS ROOT from Mac via ssh root@49.13.165.23 — adds Bruno's SSH key, ensures bruno user exists (Lisa's bootstrap may have already done this; idempotent)
    bootstrap-bruno.sh                     # idempotent, run AS BRUNO via ssh bruno@... — clones repo, runs setup.sh, installs systemd unit symlinks, creates /var/log/brunoosbrain dir
    sync-secrets.sh                        # scp .claude/.env + Google token → VPS
    install-mac-launchd.sh                 # symlinks plists into ~/Library/LaunchAgents (Disabled=true)
    rotate-postgres-password.sh            # one-shot helper, generates pw + writes to .claude/.env + ALTER ROLE on VPS
    cross-backend-smoke.py                 # SQLite↔Postgres top-5 overlap diff for memory_search regression checks
  launchd/
    com.bruno.brunos.heartbeat.plist
    com.bruno.brunos.reflection.plist
    com.bruno.brunos.weekly-review.plist
    com.bruno.brunos.news-digest.plist
    com.bruno.brunos.chat.plist
    com.bruno.brunos.git-sync.plist
  systemd/
    brunoosbrain-slackbot.service          # long-running chat bot (mirrors lisaosbrain-slackbot.service)
    brunoosbrain-heartbeat.service
    brunoosbrain-heartbeat.timer
    brunoosbrain-reflect.service
    brunoosbrain-reflect.timer
    brunoosbrain-weekly-review.service     # see NOTES — Bruno's message listed only 4 services; including weekly-review + news-digest by default since CLAUDE.md treats them as scheduled. Drop if Bruno wants the 4-unit minimum.
    brunoosbrain-weekly-review.timer
    brunoosbrain-news-digest.service
    brunoosbrain-news-digest.timer
    brunoosbrain-vault-sync.service
    brunoosbrain-vault-sync.timer
  postgres/
    init.sql                               # Bruno's role + db, CREATE EXTENSION vector, schema mirror (parallel to Lisa's role)
  vault/
    gitignore                              # template for BrunOS/.gitignore (cp at init time)
    gitattributes                          # template for BrunOS/.gitattributes
```

Plus modifications:
- `.claude/scripts/db.py` — replace the `NotImplementedError` Postgres branch with the real implementation (no rename, no public-API change).
- `pyproject.toml` — add `pgvector` to `[vps]`.
- `CLAUDE.md` — append "Phase 9" section + flip checklist.
- `.claude/.env.example` — add comment block for VPS-specific values (no new keys).

### Relevant Documentation — READ BEFORE IMPLEMENTING

- [pgvector Python adapter](https://github.com/pgvector/pgvector-python#psycopg-3) — Why: `register_vector(conn)` registration is mandatory for `np.ndarray` ↔ `vector(384)` adaptation. Use the **synchronous** `psycopg.Connection` path (the codebase is sync; don't mix asyncpg in).
- [pgvector index types](https://github.com/pgvector/pgvector#indexing) — Why: HNSW vs IVFFlat. Pick **HNSW** with `(m=16, ef_construction=64)` defaults — better recall than IVFFlat, no tuning required, and our corpus (≤a few thousand chunks) is small enough that build time isn't a concern.
- [pgvector distance operators](https://github.com/pgvector/pgvector#distances) — Why: `<=>` is cosine distance (lower = better match). Mirror SQLite's `vec0` MATCH operator semantics: return rows ordered ascending by distance.
- [Postgres `tsvector` + `websearch_to_tsquery`](https://www.postgresql.org/docs/16/textsearch-controls.html#TEXTSEARCH-PARSING-QUERIES) — Why: use `websearch_to_tsquery('english', %s)` not `plainto_tsquery` — it accepts the same `+required -excluded "phrase" or` operators the `memory-search` skill documents for SQLite FTS5, so Bruno's escape-hatch queries work on both backends. `ts_rank_cd(fts, query)` for ranking (higher = better → `ORDER BY score DESC`).
- [systemd OnCalendar syntax](https://www.freedesktop.org/software/systemd/man/systemd.time.html#Calendar%20Events) — Why: needed for the heartbeat's "every 30 min between 08:00 and 22:00 BRT". Validate every expression with `systemd-analyze calendar "<expr>"` before committing the unit.
- [systemd `LogsDirectory=` + `StandardOutput=append:`](https://www.freedesktop.org/software/systemd/man/systemd.exec.html#LogsDirectory=) — Why: Bruno wants log files at `/var/log/brunoosbrain-*.log` (matching Lisa's pattern). `LogsDirectory=brunoosbrain` creates `/var/log/brunoosbrain/` owned by the service user (no manual chown). `StandardOutput=append:/var/log/brunoosbrain/<svc>.log` and same for `StandardError=` write the file form Bruno wants.
- [launchd `StartCalendarInterval` array form](https://www.launchd.info/) — Why: launchd has no "every 30 min between 8 and 22" — you must enumerate ~30 array entries (one per slot). Generate them programmatically inside `install-mac-launchd.sh`.
- [git-sync (simonthum)](https://github.com/simonthum/git-sync#description) — Why: the script Bruno installs on both machines. Configure with `git config branch.main.sync true` and `git config branch.main.syncNewFiles true`. Read the README's section "How it handles conflicts" before relying on it.
- [Hetzner Cloud · ARM64 Ubuntu 24.04](https://docs.hetzner.com/cloud/servers/getting-started/installing-the-os) — Why: confirms ARM64 wheel availability matters. `psycopg[binary]` ships aarch64 wheels for Linux; pgvector apt is `postgresql-16-pgvector` on 24.04 ARM (Lisa already verified — confirm via `dpkg -l | grep pgvector`). FastEmbed is pure Python (works on ARM).
- [Slack `auth.test` API](https://api.slack.com/methods/auth.test) — Why: smoke-test endpoint for verifying the bot is alive and (implicitly, by counting active Socket Mode connections client-side) running on a single host.

### Patterns to Follow

**Naming Conventions:**
- Python files: `snake_case.py` (project-wide).
- Shell scripts in `deploy/bin/`: `kebab-case`. `git-merge-concat` has no extension because git invokes it by name; `*.sh` extension only on the bootstrap files for grep-ability.
- launchd plists: `com.bruno.brunos.<service>.plist` (reverse-DNS as Apple convention; `brunos` here is short for "BrunOS" agent label, NOT the deprecated `brunos` systemd prefix).
- systemd units: **`brunoosbrain-<service>.{service,timer}`** (kebab-case, mirrors Lisa's `lisaosbrain-` namespace).
- Postgres role and DB: both **`brunoosbrain`** (lower-case, single segment; parallel to Lisa's `lisaosbrain`).
- Log files: **`/var/log/brunoosbrain/<service>.log`** (created via systemd `LogsDirectory=brunoosbrain`).

**Error Handling (per `db.py` and `shared.py` patterns):**
- DB calls let `psycopg` exceptions propagate. Don't wrap in try/except unless you have a specific recovery path (mirroring `db.keyword_search`'s `OperationalError` swallow on FTS5 parse failure → `[]`; do the same for `psycopg.errors.SyntaxError` from `websearch_to_tsquery`).
- Bash scripts: `set -euo pipefail` at top of every shell file. Use `trap 'echo "FAILED at line $LINENO" >&2' ERR` for diagnostics.
- Bootstrap script idempotency: every step `if`-guarded (e.g. `id bruno >/dev/null 2>&1 || adduser …`).

**Logging Pattern:**
- Python: `_log(msg)` helpers print to `sys.stderr`, never stdout (mirrors `chat/bot.py:44` and `heartbeat.py`). Don't add `logging.*` — project doesn't use it.
- Shell: `echo "==> <step>"` with `>&2` on bootstrap output.
- systemd: `LogsDirectory=brunoosbrain` + `StandardOutput=append:/var/log/brunoosbrain/<svc>.log` + same for `StandardError=`. View with `tail -f /var/log/brunoosbrain/<svc>.log` or `journalctl -u brunoosbrain-<svc> -f` (both work — file form is what Bruno wants for parity with Lisa).

**Recursion Guard (mandatory for SDK-invoking units):**
- `brunoosbrain-slackbot.service` runs `chat/bot.py` which already sets `CLAUDE_INVOKED_BY=chat` before SDK import. **Don't** set it in the unit file (the script's `os.environ.setdefault` line owns this).
- Same for heartbeat (`heartbeat`), reflection (`reflection`), weekly-review (`weekly-review` — verify in `.claude/skills/weekly-review/scripts/aggregate_week.py`), news-digest (`news-digest` — verify in `.claude/skills/news-digest/scripts/digest.py`).

**`setting_sources` policy** — already enforced inside the scripts. Deploy units don't need to do anything special; they invoke the script and the script sets options correctly.

**Portuguese-vs-English locale:** systemd and launchd unit content stays in English (it's infrastructure). Vault content language routing remains untouched.

**Shared-host isolation (NEW):**
- Bruno's processes run as the `bruno` Linux user; Lisa's as `lisa`. Unix file permissions enforce isolation. Postgres roles are independent (`brunoosbrain` vs `lisaosbrain`); no cross-DB grants.
- Both Slack chat bots co-exist because each is a separate Slack app with its own bot token and Socket Mode connection — no port collision.
- Bruno's vault repo (`brunobouwman/brunos-vault`) is NOT shared with Lisa.
- Hardware ceiling: CX21 has 2 vCPU / 4 GB RAM. Both BrunOSes plus their heartbeats peak together at :00 and :30 — if memory pressure shows up, stagger Bruno's timers by +15s (`OnCalendar=*-*-* 08..22:15/30`).

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation (Mac, no VPS access yet)

Build the artifacts that don't depend on the droplet existing.

**Tasks:**
- Author the Postgres branch in `db.py` (this is the largest single piece of code in Phase 9).
- Author every plist, every systemd unit, every helper script in `deploy/`, plus `setup.sh` at repo root.
- Author `BrunOS/.gitignore` and `BrunOS/.gitattributes` templates under `deploy/vault/`.
- Update `pyproject.toml` (pgvector adapter to `[vps]` extra).
- Update `.claude/.env.example` (VPS-specific notes, no new keys).
- Validate locally:
  - `uv sync` succeeds (no version conflicts from adding pgvector).
  - `DB_BACKEND=sqlite` round-trip still works (no regression).
  - `DB_BACKEND=postgres POSTGRES_URL=postgresql://localhost/brunoosbrain_test` against a local Postgres (Mac dev — `brew install postgresql@16 pgvector`) round-trips: index → search → diff results vs. SQLite for the same vault.

### Phase 2: Bruno-side seed on shared host (interactive, with Bruno)

Lisa already provisioned host basics. Bruno needs the SSH key seeded and the bruno user verified.

**Tasks:**
- Get Bruno's local public key (`cat ~/.ssh/id_ed25519.pub`).
- Run `deploy/bin/seed-bruno-on-host.sh` — needs `ssh root@49.13.165.23` access. Idempotent: if the bruno user already exists with the key, exits clean.
- Append SSH config Host alias `brunoos` → `~/.ssh/config` on Mac.
- Verify `ssh brunoos id` returns `uid=… bruno …`.

### Phase 3: Bruno-user bootstrap on host

**Tasks:**
- `ssh brunoos`, then `git clone https://github.com/brunobouwman/claude-second-brain.git /home/bruno/claude-second-brain` (or `rsync` from Mac if the repo isn't yet pushed to a VPS-reachable remote).
- Run `bash setup.sh` inside the repo — creates `.venv` via `uv sync --extra vps`, ensures `/var/log/brunoosbrain/` exists with the right perms (delegated to systemd `LogsDirectory=`, but we sanity-check), and verifies `/usr/local/bin/uv --version` is present.
- `deploy/bin/sync-secrets.sh` from Mac → scp `.claude/.env` + `google_token.json` → VPS. (`google_client_secrets.json` is **not** needed at runtime — only the token.)
- Edit `/home/bruno/claude-second-brain/.claude/.env` on VPS to set:
  - `BRUNOS_VAULT_PATH=/home/bruno/BrunOS`
  - `DB_BACKEND=postgres`
  - `POSTGRES_URL=postgresql://brunoosbrain:<pw>@localhost:5432/brunoosbrain`
  - `ANTHROPIC_API_KEY=<key>`
  - All Slack/GitHub/ClickUp/Google client tokens.
  - **Do NOT set `CLAUDE_CODE_OAUTH_TOKEN`** — that's Mac desktop OAuth flow only.

### Phase 4: Vault Git-Init (Mac side, then VPS clone)

**Tasks:**
- `cd $BRUNOS_VAULT_PATH && git init && git branch -m main`.
- Copy `deploy/vault/gitignore` → `BrunOS/.gitignore`. Same for `gitattributes`.
- Create private GitHub repo `brunobouwman/brunos-vault` (manual, via `gh repo create` or web UI).
- `git remote add origin git@github.com:brunobouwman/brunos-vault.git`.
- `git add -A && git commit -m "init: vault repo" && git push -u origin main`.
- VPS: `ssh brunoos 'cd /home/bruno && git clone git@github.com:brunobouwman/brunos-vault.git BrunOS'` (deploy key on VPS; see Step 21 GOTCHA).
- Run `deploy/bin/install-merge-driver.sh` on **both** machines (`git config` is per-repo, not committed).
- Smoke-test: append a line to `BrunOS/Memory/daily/$(date +%F).md` on Mac, wait ≤ 4 min, verify it appears on VPS.

### Phase 5: Postgres init (Bruno's role + DB on shared instance)

Lisa's `lisaosbrain` role/DB already exist; Bruno's gets created in parallel. Same Postgres instance, isolated by role.

**Tasks:**
- Generate Postgres password: `openssl rand -base64 24` → set as `POSTGRES_PASSWORD` env on Mac.
- `ssh brunoos 'sudo -u postgres psql -v pw="$POSTGRES_PASSWORD" -f /home/bruno/claude-second-brain/deploy/postgres/init.sql'`.
- Cold-build the index: `ssh brunoos 'cd /home/bruno/claude-second-brain && /usr/local/bin/uv run python .claude/scripts/memory_index.py --full'`.
- Smoke-test query: `ssh brunoos 'cd /home/bruno/claude-second-brain && /usr/local/bin/uv run python .claude/scripts/memory_search.py "what did I write about Vertik this week"'` returns rows that look right.

### Phase 6: systemd Install + Enable (VPS, Bruno's namespace)

**Tasks:**
- Symlink (don't copy — ease of post-deploy edits) every unit from `/home/bruno/claude-second-brain/deploy/systemd/` into `/etc/systemd/system/`.
- `sudo systemctl daemon-reload`.
- `sudo systemctl enable --now brunoosbrain-vault-sync.timer brunoosbrain-heartbeat.timer brunoosbrain-reflect.timer brunoosbrain-weekly-review.timer brunoosbrain-news-digest.timer brunoosbrain-slackbot.service`.
- Verify each: `systemctl status brunoosbrain-<unit>`, `tail -50 /var/log/brunoosbrain/<unit>.log`.
- Sanity-check Bruno doesn't collide with Lisa: `systemctl list-units --type=service --state=active | grep -E '(brunos|lisas)osbrain'` should show both namespaces co-existing.

### Phase 7: Mac Launchd Install (failover-ready, disabled by default)

**Tasks:**
- Run `deploy/bin/install-mac-launchd.sh` — symlinks plists into `~/Library/LaunchAgents/`. Each plist has `Disabled=true` baked in.
- Vault git-sync on Mac is the **one exception** — it loads enabled (it's a read consumer; harmless to dual-run with VPS git-sync).
- Document the failover one-liner in `deploy/README.md`: `defaults write ~/Library/LaunchAgents/com.bruno.brunos.<svc>.plist Disabled -bool false && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bruno.brunos.<svc>.plist`.

### Phase 8: End-to-end Validation + Cutover

**Tasks:**
- Slack DM the bot from a phone (Mac asleep) — verify single reply within seconds.
- Wait for next :00 or :30 BRT — verify VPS heartbeat fires (`tail -f /var/log/brunoosbrain/heartbeat.log` and `journalctl -u brunoosbrain-heartbeat -n 50`) and writes to today's daily log; confirm log appears on Mac via git-sync within 2 min.
- Force a write race on the daily log (manual append on both ends within 30s of each other) → verify both lines survive after sync.
- Confirm Lisa's services were not impacted: `systemctl status lisaosbrain-*` still active (Lisa-side validation — coordinate with her).
- Update `CLAUDE.md` Phase 9 section + flip phase status to `[x]`.

---

## STEP-BY-STEP TASKS

Execute in order. Each task is atomic and independently testable. Validation commands assume cwd = repo root unless noted.

### 1. CREATE one-off "seed Bruno's SSH key on the host" command (no file artifact yet — manual root SSH)

- **IMPLEMENT**: Run from Mac, with Bruno's public key value substituted in. This is the **only** step that requires root SSH access to 49.13.165.23 (assuming Lisa wired root SSH for handover; if not, ask her to add the key directly).
  ```bash
  PUBKEY="$(cat ~/.ssh/id_ed25519.pub)"
  ssh root@49.13.165.23 "
    set -e
    id bruno >/dev/null 2>&1 || adduser --disabled-password --gecos '' bruno
    install -d -m 700 -o bruno -g bruno /home/bruno/.ssh
    grep -qxF '$PUBKEY' /home/bruno/.ssh/authorized_keys 2>/dev/null || echo '$PUBKEY' >> /home/bruno/.ssh/authorized_keys
    chmod 600 /home/bruno/.ssh/authorized_keys
    chown bruno:bruno /home/bruno/.ssh/authorized_keys
    grep -q '^bruno ALL=' /etc/sudoers.d/bruno 2>/dev/null || { echo 'bruno ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/bruno; chmod 440 /etc/sudoers.d/bruno; }
  "
  ```
- **PATTERN**: Mirror Lisa's onboarding (already done). The `install -d`/`grep -qxF` idiom keeps this re-runnable.
- **GOTCHA**: If Bruno's pubkey contains spaces (it should — `ssh-ed25519 AAAA… comment`), the heredoc-style quoting above handles it. Don't drop the outer double-quotes.
- **VALIDATE**: `ssh -o BatchMode=yes bruno@49.13.165.23 id` returns `uid=… bruno …` without prompting for a password.

### 2. CREATE `deploy/bin/seed-bruno-on-host.sh` (file form of step 1)

- **IMPLEMENT**: Same logic as step 1, packaged as a script for re-runs. Reads pubkey from `$1` or `~/.ssh/id_ed25519.pub`.
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  trap 'echo "FAILED at line $LINENO" >&2' ERR
  PUBKEY="${1:-$(cat ~/.ssh/id_ed25519.pub)}"
  : "${VPS_HOST:=root@49.13.165.23}"
  ssh "$VPS_HOST" "PUBKEY='$PUBKEY' bash -s" <<'REMOTE'
    set -euo pipefail
    id bruno >/dev/null 2>&1 || adduser --disabled-password --gecos '' bruno
    install -d -m 700 -o bruno -g bruno /home/bruno/.ssh
    touch /home/bruno/.ssh/authorized_keys
    grep -qxF "$PUBKEY" /home/bruno/.ssh/authorized_keys || echo "$PUBKEY" >> /home/bruno/.ssh/authorized_keys
    chmod 600 /home/bruno/.ssh/authorized_keys
    chown bruno:bruno /home/bruno/.ssh/authorized_keys
    grep -q '^bruno ALL=' /etc/sudoers.d/bruno 2>/dev/null || { echo 'bruno ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/bruno; chmod 440 /etc/sudoers.d/bruno; }
    echo "==> bruno user seeded; sudo NOPASSWD set"
  REMOTE
  ```
- **GOTCHA**: Quoting `$PUBKEY` through nested SSH is the bug-prone part — using a heredoc and passing via `PUBKEY=…` env on the remote shell is cleaner than nested escaping.
- **VALIDATE**: Re-run twice; second run prints `==> bruno user seeded` without errors.

### 3. CREATE `setup.sh` at repo root

- **IMPLEMENT**: Idempotent dev/VPS bootstrap. Used both by Bruno's local Mac onboarding and by `bootstrap-bruno.sh` on the VPS.
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  trap 'echo "FAILED at line $LINENO" >&2' ERR

  ROOT="$(cd "$(dirname "$0")" && pwd)"
  cd "$ROOT"

  echo "==> verify uv"
  if ! command -v uv >/dev/null; then
    echo "ERROR: uv not on PATH. On VPS this should be /usr/local/bin/uv (Lisa's setup)." >&2
    exit 1
  fi
  uv --version

  echo "==> uv sync (with vps extras when DB_BACKEND=postgres)"
  if [[ "${DB_BACKEND:-sqlite}" == "postgres" ]]; then
    uv sync --extra vps
  else
    uv sync
  fi

  echo "==> sanity import check"
  uv run python -c "import claude_agent_sdk, fastembed; print('ok')"
  if [[ "${DB_BACKEND:-sqlite}" == "postgres" ]]; then
    uv run python -c "import psycopg; from pgvector.psycopg import register_vector; print('pg ok')"
  fi

  echo "==> done."
  ```
- **PATTERN**: Mirror Lisa's `setup.sh` shape (read hers via `ssh root@... cat /home/lisa/.../setup.sh` if you can; otherwise this is fine).
- **GOTCHA**: Don't `chmod` the env file or pre-create `/var/log/brunoosbrain/` here — systemd's `LogsDirectory=brunoosbrain` does that on first service start.
- **VALIDATE**: `bash setup.sh` on Mac (DB_BACKEND unset) passes; `DB_BACKEND=postgres bash setup.sh` on VPS passes.

### 4. UPDATE `pyproject.toml`

- **IMPLEMENT**: Add `pgvector>=0.3,<0.4` to the existing `[vps]` extra (line 32).
- **PATTERN**: Match the trailing-comma style of `[dependencies]`.
- **GOTCHA**: Don't add to top-level dependencies — Mac shouldn't pull pgvector. Phase 9 says it explicitly. Verify `uv pip list | grep pgvector` returns empty after a plain `uv sync` and present after `uv sync --extra vps`.
- **VALIDATE**: `uv sync` and `uv sync --extra vps` both succeed.

### 5. UPDATE `.claude/scripts/db.py` — add Postgres branch

- **IMPLEMENT**:
  - Top-of-file: gate `import sqlite3` and `import sqlite_vec` behind `BACKEND` so the VPS doesn't need sqlite-vec installed:
    ```python
    BACKEND = os.environ.get("DB_BACKEND", "sqlite")
    if BACKEND == "sqlite":
        import sqlite3
        import sqlite_vec
    elif BACKEND == "postgres":
        import psycopg
        import psycopg.rows
        from pgvector.psycopg import register_vector
    else:
        raise RuntimeError(f"unknown DB_BACKEND={BACKEND!r}")
    ```
  - Replace the `connect()` body with a backend dispatch. SQLite path stays unchanged. Postgres path:
    ```python
    url = os.environ["POSTGRES_URL"]
    conn = psycopg.connect(url, autocommit=False, row_factory=psycopg.rows.dict_row)
    register_vector(conn)
    return conn
    ```
  - Add a `_PG_SCHEMA` constant mirroring `deploy/postgres/init.sql` so `init_schema(conn)` is a safety net when running against a fresh DB. On Postgres, `init_schema` runs `CREATE EXTENSION IF NOT EXISTS vector;` and `CREATE TABLE IF NOT EXISTS chunks (…)` etc.
  - Add a tiny `_is_postgres(conn)` helper (or just dispatch via `BACKEND` constant — simpler).
  - Refactor each query function to dispatch:
    - `upsert_chunk`: SQLite path unchanged. Postgres path: `INSERT … ON CONFLICT (file_path, chunk_idx) DO UPDATE SET content=EXCLUDED.content, mtime=EXCLUDED.mtime, embedding=EXCLUDED.embedding RETURNING id`. Pass the `np.ndarray` directly — pgvector's adapter handles it.
    - `delete_chunks_for_file`: Postgres single statement `DELETE FROM chunks WHERE file_path = %s RETURNING id` — count rows.
    - `vector_search`: Postgres `SELECT id, file_path, chunk_idx, content, (embedding <=> %s) AS distance FROM chunks [WHERE file_path LIKE %s || '/%'] ORDER BY embedding <=> %s LIMIT %s`. **Bind the qemb twice** (once for distance, once for ORDER BY) — Postgres doesn't reuse the alias inside ORDER BY in all index plans; binding twice forces the planner to use the HNSW index. Verify with `EXPLAIN`.
    - `keyword_search`: Postgres `SELECT id, file_path, chunk_idx, content, ts_rank_cd(fts, q) AS score FROM chunks, websearch_to_tsquery('english', %s) q WHERE fts @@ q [AND file_path LIKE %s || '/%'] ORDER BY score DESC LIMIT %s`. Wrap in try/except `psycopg.errors.SyntaxError` → return `[]` (mirroring SQLite FTS5's swallow at lines 156-158).
    - `all_file_mtimes`: identical SQL, just `%s` placeholder style.
    - `get_chunks`: Postgres `WHERE id = ANY(%s)` with the ids list as a single param.
  - Keep `EMBED_DIM = 384` exported.
- **PATTERN**: Mirror SQLite path's row-dict shape exactly. `psycopg.rows.dict_row` already returns `dict`s — don't wrap. Make sure key names match (`file_path`, `chunk_idx`, `content`, `id`, `distance`, `score`).
- **IMPORTS**: `import psycopg`, `import psycopg.rows`, `from pgvector.psycopg import register_vector`. Keep these inside the `BACKEND == "postgres"` branch so Mac doesn't need them.
- **GOTCHA #1**: SQLite returns `bm25` (lower=better, `ORDER BY score`); Postgres returns `ts_rank_cd` (higher=better, `ORDER BY score DESC`). RRF only consumes ordinal rank, so this asymmetry is fine — but **don't** change `memory_search.py`'s rrf_fuse code expecting score signs. The score field is informational only.
- **GOTCHA #2**: numpy → pgvector adaptation requires `register_vector(conn)` AFTER `psycopg.connect`. If you forget, you'll get `adapter not found for class numpy.ndarray`.
- **GOTCHA #3**: `psycopg.connect` defaults to `autocommit=False`. `memory_index.py` should call `conn.commit()` at end-of-file (verify); if not, either add `conn.commit()` inside `upsert_chunk` after each insert OR set `autocommit=True`. Pick `autocommit=False` and let the indexer commit at end-of-file (matches SQLite semantics).
- **GOTCHA #4**: `LIKE %s || '/%'` — the `%` in `'/%'` is a SQL literal, not a parameter placeholder. psycopg uses `%s` for params and treats `%` in literals fine, but if you ever switch to `%(named)s`-style params you must double the literal `%` to `%%`. Stick with `%s`.
- **VALIDATE**:
  - `DB_BACKEND=sqlite uv run python .claude/scripts/memory_index.py --full --dry-run` (no behavior change).
  - `DB_BACKEND=sqlite uv run python .claude/scripts/memory_search.py "test query"` (returns same shape as before).
  - With local Postgres: `DB_BACKEND=postgres POSTGRES_URL=postgresql://localhost/brunoosbrain_test uv run python .claude/scripts/memory_index.py --full`, then `… memory_search.py "test query"`. Compare top-5 rows vs. SQLite — file_path overlap should be ≥3/5.
  - Diff test: `deploy/bin/cross-backend-smoke.py` (Step 18) reports overlap.

### 6. CREATE `deploy/postgres/init.sql`

- **IMPLEMENT**:
  ```sql
  -- Run as postgres superuser. Idempotent — safe to re-run.
  -- Invoked: sudo -u postgres psql -v pw="<password>" -f deploy/postgres/init.sql
  -- Coexists with Lisa's lisaosbrain role/DB on the same Postgres instance.

  DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='brunoosbrain') THEN
      EXECUTE format('CREATE ROLE brunoosbrain WITH LOGIN PASSWORD %L', current_setting('myvars.pw'));
    END IF;
  END $$;

  SELECT 'CREATE DATABASE brunoosbrain OWNER brunoosbrain'
   WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname='brunoosbrain')\gexec

  \c brunoosbrain
  CREATE EXTENSION IF NOT EXISTS vector;

  CREATE TABLE IF NOT EXISTS chunks (
    id          BIGSERIAL PRIMARY KEY,
    file_path   TEXT NOT NULL,
    chunk_idx   INTEGER NOT NULL,
    content     TEXT NOT NULL,
    mtime       DOUBLE PRECISION NOT NULL,
    embedding   vector(384) NOT NULL,
    fts         tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    UNIQUE (file_path, chunk_idx)
  );
  CREATE INDEX IF NOT EXISTS chunks_file_path_idx ON chunks (file_path);
  CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
  CREATE INDEX IF NOT EXISTS chunks_fts_idx       ON chunks USING gin (fts);
  GRANT ALL ON SCHEMA public TO brunoosbrain;
  GRANT ALL ON ALL TABLES IN SCHEMA public TO brunoosbrain;
  GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO brunoosbrain;
  ```
  Pass the password via psql `-v`: `sudo -u postgres psql -v pw="$POSTGRES_PASSWORD" -v ON_ERROR_STOP=1 -f init.sql`. To make `current_setting('myvars.pw')` resolve, set it via `\set` first OR (simpler) use `psql -v` plus `:'pw'` interpolation. Pick whichever style matches Lisa's init script — pull hers as the canonical reference.
- **PATTERN**: SQLite schema in `db.py` lines 21-50 (single chunks table, file_path+chunk_idx unique). Mirror this — don't introduce a separate `files` table.
- **GOTCHA**: HNSW index build is fast for our scale (≤a few thousand chunks). Don't tune `m` / `ef_construction` — defaults are fine. Document in init.sql that Bruno can `REINDEX INDEX chunks_embedding_idx` if recall ever degrades.
- **GOTCHA**: Lisa's `lisaosbrain` role/DB already exist on this same Postgres instance. The `IF NOT EXISTS` guards above prevent collision; do **not** `DROP` anything.
- **VALIDATE**: `psql -h localhost -U brunoosbrain brunoosbrain -c "\d chunks"` shows the table with all 4 indexes. `psql -c "SELECT extname FROM pg_extension WHERE extname='vector'"` returns `vector`. `\du` shows both `brunoosbrain` and `lisaosbrain` as separate roles.

### 7. CREATE `deploy/launchd/com.bruno.brunos.heartbeat.plist`

- **IMPLEMENT**: Standard launchd plist with `Disabled=true`, `Label=com.bruno.brunos.heartbeat`, `ProgramArguments=[<uv-bin>, "run", "python", ".claude/scripts/heartbeat.py"]`, `WorkingDirectory=/Users/brunobouwman/Documents/claude-second-brain`, `EnvironmentVariables={TZ=America/Sao_Paulo, PATH=/Users/brunobouwman/.local/bin:/usr/local/bin:/usr/bin:/bin}`. `StandardErrorPath=/Users/brunobouwman/Documents/claude-second-brain/.claude/data/state/heartbeat.err.log`, `StandardOutPath=/dev/null`. Schedule: `StartCalendarInterval` array of 60 entries (`Hour: 8..22, Minute: 0` and `Minute: 30`). Generate the plist programmatically inside `install-mac-launchd.sh` rather than hand-writing 60 dict entries.
- **PATTERN**: Use [launchd plist tutorial](https://www.launchd.info/) as reference.
- **GOTCHA**: launchd ignores `EnvironmentVariables` for `PATH` if the plist is loaded via `launchctl bootstrap` without `--no-passive`; verify your `uv` binary path resolves at runtime (`launchctl print gui/$(id -u)/com.bruno.brunos.heartbeat | grep PATH`). On Mac the `uv` binary is per-user (`/Users/brunobouwman/.local/bin/uv`); on VPS it's system-wide (`/usr/local/bin/uv`) — don't conflate the two.
- **VALIDATE**: `plutil -lint deploy/launchd/com.bruno.brunos.heartbeat.plist` → `OK`. After install: `launchctl print gui/$(id -u)/com.bruno.brunos.heartbeat` shows the unit registered with `state = not running` (because Disabled).

### 8. CREATE the other 5 launchd plists

- **IMPLEMENT**: Apply the heartbeat template to:
  - `com.bruno.brunos.reflection.plist` — runs `.claude/scripts/memory_reflect.py`, daily 08:00 BRT (`StartCalendarInterval={Hour: 8, Minute: 0}`).
  - `com.bruno.brunos.weekly-review.plist` — runs `.claude/skills/weekly-review/scripts/aggregate_week.py`, Sundays 19:00 BRT (`Weekday=0, Hour=19, Minute=0`).
  - `com.bruno.brunos.news-digest.plist` — runs `.claude/skills/news-digest/scripts/digest.py`, daily 07:30 BRT.
  - `com.bruno.brunos.chat.plist` — runs `.claude/chat/bot.py`, `KeepAlive=true`, `RunAtLoad=true`. **Single-instance mandatory** — ships `Disabled=true` like the rest.
  - `com.bruno.brunos.git-sync.plist` — runs `git-sync` (the simonthum script — install path: `/usr/local/bin/git-sync` after `brew install git-sync` or manual install) inside `BrunOS/`, every 2 min (`StartInterval=120`). **This one ships `Disabled=false`** — Mac is a read consumer; harmless to dual-run with VPS git-sync.
- **VALIDATE**: `plutil -lint deploy/launchd/*.plist` all return OK.

### 9. CREATE `deploy/systemd/brunoosbrain-heartbeat.service` + `.timer`

- **IMPLEMENT**:
  ```ini
  # brunoosbrain-heartbeat.service
  [Unit]
  Description=brunoosbrain heartbeat
  After=network-online.target postgresql.service
  Wants=network-online.target

  [Service]
  Type=oneshot
  User=bruno
  Group=bruno
  WorkingDirectory=/home/bruno/claude-second-brain
  EnvironmentFile=/home/bruno/claude-second-brain/.claude/.env
  Environment=TZ=America/Sao_Paulo
  Environment=PATH=/usr/local/bin:/usr/bin:/bin
  ExecStart=/usr/local/bin/uv run python .claude/scripts/heartbeat.py
  TimeoutStartSec=300
  LogsDirectory=brunoosbrain
  StandardOutput=append:/var/log/brunoosbrain/heartbeat.log
  StandardError=append:/var/log/brunoosbrain/heartbeat.log
  ```
  ```ini
  # brunoosbrain-heartbeat.timer
  [Unit]
  Description=brunoosbrain heartbeat — every 30 min between 08:00 and 22:00 BRT

  [Timer]
  OnCalendar=*-*-* 08..22:00/30 America/Sao_Paulo
  Persistent=false
  Unit=brunoosbrain-heartbeat.service

  [Install]
  WantedBy=timers.target
  ```
- **PATTERN**: Mirror Lisa's `lisaosbrain-heartbeat.{service,timer}` exactly — pull hers via `ssh root@49.13.165.23 cat /etc/systemd/system/lisaosbrain-heartbeat.service` and diff structurally. The unit naming, `User=`, `WorkingDirectory=`, log paths are the only differences.
- **PATTERN**: Read [systemd.timer man](https://www.freedesktop.org/software/systemd/man/systemd.timer.html) and verify `Persistent=false` (we don't want missed ticks to fire on boot — they're 30-min cadence; one missed tick is fine).
- **GOTCHA**: `OnCalendar` with `America/Sao_Paulo` requires systemd 244+ (Ubuntu 24.04 ships 255 — fine). Validate with `systemd-analyze calendar "*-*-* 08..22:00/30 America/Sao_Paulo"` — output must show next run within 30 min during BRT business hours.
- **GOTCHA**: `LogsDirectory=brunoosbrain` creates `/var/log/brunoosbrain/` owned by `bruno:bruno` mode 0755 on first start — no manual `mkdir`/`chown` needed. Verify with `stat /var/log/brunoosbrain` after first run.
- **VALIDATE**: `systemd-analyze verify deploy/systemd/brunoosbrain-heartbeat.{service,timer}` → no errors.

### 10. CREATE the other 4 timers + `brunoosbrain-slackbot.service` + `brunoosbrain-vault-sync` pair

- **IMPLEMENT**: Apply the heartbeat template:
  - `brunoosbrain-reflect.timer`: `OnCalendar=*-*-* 08:00 America/Sao_Paulo`. Service runs `.claude/scripts/memory_reflect.py`, log to `/var/log/brunoosbrain/reflect.log`.
  - `brunoosbrain-weekly-review.timer`: `OnCalendar=Sun *-*-* 19:00 America/Sao_Paulo`. Service runs `.claude/skills/weekly-review/scripts/aggregate_week.py`, log to `/var/log/brunoosbrain/weekly-review.log`. **(Optional — Bruno's message listed only 4 services. Drop this pair if he wants the minimum set.)**
  - `brunoosbrain-news-digest.timer`: `OnCalendar=*-*-* 07:30 America/Sao_Paulo`. Service runs `.claude/skills/news-digest/scripts/digest.py`, log to `/var/log/brunoosbrain/news-digest.log`. **(Same optionality as weekly-review.)**
  - `brunoosbrain-slackbot.service` (no timer, long-running):
    ```ini
    [Unit]
    Description=brunoosbrain Slack chat bot
    After=network-online.target
    Wants=network-online.target

    [Service]
    Type=simple
    Restart=on-failure
    RestartSec=10
    User=bruno
    Group=bruno
    WorkingDirectory=/home/bruno/claude-second-brain
    EnvironmentFile=/home/bruno/claude-second-brain/.claude/.env
    Environment=TZ=America/Sao_Paulo
    Environment=PATH=/usr/local/bin:/usr/bin:/bin
    ExecStart=/usr/local/bin/uv run python .claude/chat/bot.py
    LogsDirectory=brunoosbrain
    StandardOutput=append:/var/log/brunoosbrain/slackbot.log
    StandardError=append:/var/log/brunoosbrain/slackbot.log

    [Install]
    WantedBy=multi-user.target
    ```
  - `brunoosbrain-vault-sync.{service,timer}`: timer `OnCalendar=*:0/2` (every 2 min). Service `Type=oneshot`, `User=bruno`, `WorkingDirectory=/home/bruno/BrunOS`, `ExecStart=/usr/local/bin/git-sync`, log to `/var/log/brunoosbrain/vault-sync.log`. (Note name change from `git-sync` → `vault-sync` per Bruno's namespacing in his message.)
- **VALIDATE**: `systemd-analyze verify deploy/systemd/*.{service,timer}` → no errors for any.

### 11. CREATE `deploy/bin/git-merge-concat`

- **IMPLEMENT**: Exactly the script from PRD §9.4:
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  # args: %O (ancestor) %A (local/current) %B (remote/other) %P (path)
  ANCESTOR="$1"; LOCAL="$2"; REMOTE="$3"; OUTPUT_PATH="$4"
  cp "$REMOTE" "$LOCAL.merged"
  comm -23 <(sort -u "$LOCAL") <(sort -u "$REMOTE") | while IFS= read -r line; do
    grep -Fxq "$line" "$LOCAL.merged" || printf '%s\n' "$line" >> "$LOCAL.merged"
  done
  mv "$LOCAL.merged" "$LOCAL"
  exit 0
  ```
  `chmod +x deploy/bin/git-merge-concat`.
- **GOTCHA**: This driver intentionally drops line ORDER (it sorts to compute the diff). For `Memory/daily/*.md` and `Memory/HABITS.md` that's acceptable — they're chronological appends and Bruno reads them top-to-bottom-by-timestamp anyway. Document in `deploy/README.md`.
- **VALIDATE**: `bash deploy/bin/git-merge-concat <(echo a) <(printf 'a\nb\n') <(printf 'a\nc\n') /dev/null` produces `a`, `b`, `c` (one per line, possibly reordered) on stdout via `cat $LOCAL` afterward.

### 12. CREATE `deploy/bin/install-merge-driver.sh`

- **IMPLEMENT**:
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  # Run from inside the vault repo (cwd = $BRUNOS_VAULT_PATH).
  test -d .git || { echo "ERROR: cwd must be a git repo"; exit 1; }
  REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
  git config merge.concat-both.name "Concat both sides for append-only files"
  git config merge.concat-both.driver "$REPO_ROOT/deploy/bin/git-merge-concat %O %A %B %P"
  echo "Registered merge.concat-both driver: $(git config merge.concat-both.driver)"
  ```
- **GOTCHA**: This script must be run on **both** Mac and VPS (git config is per-clone, not committed). The `REPO_ROOT` path differs: on Mac it'll resolve to `/Users/brunobouwman/Documents/claude-second-brain/`, on VPS to `/home/bruno/claude-second-brain/`.
- **VALIDATE**: After running inside `BrunOS/` on Mac: `git config merge.concat-both.driver` returns the expected absolute path. Repeat on VPS.

### 13. CREATE `deploy/vault/gitignore` and `deploy/vault/gitattributes`

- **IMPLEMENT**:
  ```
  # deploy/vault/gitignore — copied to BrunOS/.gitignore at vault-init time
  Memory/drafts/active/*
  Memory/personal/finance.md
  .DS_Store
  .obsidian/workspace*
  .obsidian/cache
  ```
  ```
  # deploy/vault/gitattributes — copied to BrunOS/.gitattributes
  Memory/daily/*.md merge=concat-both
  Memory/HABITS.md  merge=concat-both
  ```
- **GOTCHA**: `Memory/drafts/active/*` is sensitive — recipient context, partial replies. Never goes through git. `Memory/personal/finance.md` per the SOUL.md no-financial-data boundary.
- **VALIDATE**: After init, `cd BrunOS && git status --ignored` shows `Memory/drafts/active/` as ignored.

### 14. CREATE `deploy/bin/bootstrap-bruno.sh`

- **IMPLEMENT**: Idempotent script that runs **as bruno** on the VPS (after Step 1/2 seeded the user). Lisa already installed every system package + Postgres + uv, so this script is **dramatically smaller than the original DigitalOcean version**.
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  trap 'echo "FAILED at line $LINENO" >&2' ERR

  REPO=/home/bruno/claude-second-brain
  REMOTE=https://github.com/brunobouwman/claude-second-brain.git

  echo "==> verify host basics (Lisa already provisioned these)"
  command -v /usr/local/bin/uv >/dev/null || { echo "ERROR: /usr/local/bin/uv missing — Lisa's setup expected"; exit 1; }
  command -v git              >/dev/null || { echo "ERROR: git missing — Lisa's setup expected"; exit 1; }
  systemctl is-active postgresql >/dev/null || { echo "ERROR: postgresql not running"; exit 1; }
  command -v git-sync >/dev/null || {
    echo "==> install git-sync (simonthum) — not part of Lisa's host setup"
    sudo curl -fsSL https://raw.githubusercontent.com/simonthum/git-sync/master/git-sync \
      -o /usr/local/bin/git-sync
    sudo chmod +x /usr/local/bin/git-sync
  }

  echo "==> clone repo (or git pull if already present)"
  if [[ -d "$REPO/.git" ]]; then
    git -C "$REPO" pull --ff-only
  else
    git clone "$REMOTE" "$REPO"
  fi

  echo "==> setup.sh"
  cd "$REPO"
  DB_BACKEND=postgres bash setup.sh

  echo "==> systemd unit symlinks"
  for unit in "$REPO"/deploy/systemd/*.{service,timer}; do
    [[ -f "$unit" ]] || continue
    sudo ln -sf "$unit" "/etc/systemd/system/$(basename "$unit")"
  done
  sudo systemctl daemon-reload

  echo "==> done. Next: scp .claude/.env + google_token.json from Mac, then 'sudo systemctl enable --now brunoosbrain-...'"
  ```
- **GOTCHA #1**: This script does NOT install apt packages, NOT install Postgres, NOT touch sshd or ufw — Lisa already did all of that. Don't re-add those steps; the goal is the smallest possible diff on her working host.
- **GOTCHA #2**: If git-sync is already on the host (Lisa might have installed it for her vault-sync), the `command -v git-sync` guard skips the install. Fine.
- **GOTCHA #3**: The Postgres role/DB creation is intentionally NOT in this script — it needs `sudo -u postgres psql -v pw=…` with the password supplied by the operator. That's Step 21 (separate runbook step).
- **VALIDATE**: After running on a fresh `bruno` user: `id bruno` succeeds, `ls /home/bruno/claude-second-brain/.venv/bin/python` exists, `systemctl list-unit-files | grep brunoosbrain` shows all the units. Re-run; second run prints `==> done.` without errors.

### 15. CREATE `deploy/bin/sync-secrets.sh`

- **IMPLEMENT**:
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  : "${VPS_HOST:=brunoos}"   # SSH alias from ~/.ssh/config (User=bruno, HostName=49.13.165.23)
  REPO="$(cd "$(dirname "$0")/../.." && pwd)"
  scp "$REPO/.claude/.env"                                    "$VPS_HOST:/home/bruno/claude-second-brain/.claude/.env"
  scp "$REPO/.claude/data/state/google_token.json"            "$VPS_HOST:/home/bruno/claude-second-brain/.claude/data/state/"
  ssh "$VPS_HOST" "chmod 600 /home/bruno/claude-second-brain/.claude/.env"
  echo "==> sync done. Verify on VPS:"
  echo "      ssh $VPS_HOST 'head -5 /home/bruno/claude-second-brain/.claude/.env && stat -c %a /home/bruno/claude-second-brain/.claude/.env'"
  echo "==> remember to edit on VPS:"
  echo "      BRUNOS_VAULT_PATH=/home/bruno/BrunOS"
  echo "      DB_BACKEND=postgres"
  echo "      POSTGRES_URL=postgresql://brunoosbrain:<pw>@localhost:5432/brunoosbrain"
  echo "      ANTHROPIC_API_KEY=<your key>"
  echo "      (do NOT set CLAUDE_CODE_OAUTH_TOKEN — desktop OAuth, Mac-only)"
  ```
- **GOTCHA**: This is the **only** point in the deploy where secrets transit the wire. Use scp (encrypted), not rsync over plain ssh. Don't commit the resulting `.env` anywhere. Note: `google_client_secrets.json` is **NOT** scp'd — only `google_token.json` is needed at runtime (`_google.py:23` reads `GOOGLE_OAUTH_TOKEN_PATH`).
- **VALIDATE**: After running, `ssh brunoos 'head -5 /home/bruno/claude-second-brain/.claude/.env'` shows the env, and the file is mode 600.

### 16. CREATE `deploy/bin/install-mac-launchd.sh`

- **IMPLEMENT**:
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  REPO="$(cd "$(dirname "$0")/../.." && pwd)"
  TARGET="$HOME/Library/LaunchAgents"
  mkdir -p "$TARGET"
  for plist in "$REPO/deploy/launchd"/*.plist; do
    name="$(basename "$plist")"
    ln -sf "$plist" "$TARGET/$name"
    plutil -lint "$plist" >/dev/null
  done
  echo "==> linked $(ls "$REPO/deploy/launchd" | wc -l | tr -d ' ') plists into $TARGET"
  echo "==> to ENABLE a unit (e.g. failover): launchctl bootstrap gui/$(id -u) $TARGET/com.bruno.brunos.<svc>.plist"
  echo "==> to LOAD vault git-sync (recommended now): launchctl bootstrap gui/$(id -u) $TARGET/com.bruno.brunos.git-sync.plist"
  ```
- **VALIDATE**: After running, `ls -la ~/Library/LaunchAgents/com.bruno.brunos.*` shows symlinks; `launchctl print gui/$(id -u)` doesn't list them yet (they're not loaded).

### 17. CREATE `deploy/bin/rotate-postgres-password.sh`

- **IMPLEMENT**: Helper that generates a 24-byte base64 password, writes it to `.claude/.env` (replacing existing `POSTGRES_URL` if present), and runs `ALTER ROLE brunoosbrain WITH PASSWORD '...'` on the VPS via ssh. Idempotent.
- **GOTCHA**: `.claude/.env` is gitignored — but back it up via `cp` before sed-replacing in case the sed regex misfires.
- **VALIDATE**: After running, `psql "$POSTGRES_URL" -c 'SELECT 1'` succeeds with the new password.

### 18. CREATE `deploy/bin/cross-backend-smoke.py`

- **IMPLEMENT**: Small Python CLI that takes two JSON files (SQLite results + Postgres results from the same `memory_search.py` query) and asserts top-5 file_path overlap ≥3. Used as a regression check so changes to either backend don't silently drift.
- **PATTERN**: Mirror the `_log` to-stderr pattern from `chat/bot.py:44`.
- **VALIDATE**: Runs in <1s; exits non-zero when overlap drops below threshold.

### 19. CREATE `deploy/README.md`

- **IMPLEMENT**: Operator runbook covering:
  - **Host shape**: Hetzner CX21 ARM64, 49.13.165.23, **shared with Lisa**. Bruno = `bruno` user + `brunoosbrain-*` namespace. Lisa = `lisa` user + `lisaosbrain-*` namespace. Same Postgres instance, separate roles+DBs. Same git-sync binary, separate vault repos.
  - **First-time deploy sequence** (steps 1–8 in this plan, mapped to commands), including the Step 1 root SSH for SSH-key seeding.
  - **Failover Mac→VPS**: `defaults write … Disabled -bool false && launchctl bootstrap …` for each unit; reverse to fail back. **CRITICAL**: stop the VPS chat bot first (`ssh brunoos sudo systemctl stop brunoosbrain-slackbot`) — running both at once causes duplicate Slack replies.
  - **Single-instance verification**: send a Slack DM, confirm exactly one reply.
  - **Concat-both merge driver caveats** (drops line order in the merged section — fine for daily logs, not for everything).
  - **Snapshot cold-start behaviour on failover**: heartbeat's first tick on the new host treats everything as new.
  - **OAuth refresh-token expiry** (7d if consent screen is in Testing — instruct Bruno to flip to "In Production / Self-Published").
  - **Where logs live**:
    - VPS file form: `/var/log/brunoosbrain/<svc>.log` (tail -f friendly).
    - VPS journal form: `journalctl -u brunoosbrain-<svc> -f`.
    - Mac: `~/Library/Logs/com.bruno.brunos.*` via plist `StandardErrorPath`.
  - **Coexistence checklist with Lisa**: never `systemctl stop lisaosbrain-*`; never `DROP ROLE lisaosbrain`; never edit `/home/lisa/...`. If you accidentally break her stuff, slack her immediately.
- **VALIDATE**: `wc -l deploy/README.md` ≥ 100 lines; cross-reference with each `deploy/bin/*` script.

### 20. UPDATE `.claude/.env.example`

- **IMPLEMENT**: Add a comment block above `BRUNOS_VAULT_PATH` explaining the Mac-vs-VPS split (already partially there). Add a comment above `DB_BACKEND` explicitly noting "set to `postgres` on VPS, `sqlite` on Mac". Add a comment above the (still commented) `ANTHROPIC_API_KEY=` line saying "MUST be set on VPS". Add a comment block warning **"Do NOT set `CLAUDE_CODE_OAUTH_TOKEN` on VPS — desktop OAuth, Mac-only"**.
- **GOTCHA**: Do NOT commit any actual secret value. Only comments and `KEY=` empty lines. The `block-secrets.py` PreToolUse hook will refuse to read `.env*` paths — to view the existing example use `git show HEAD:.claude/.env.example` or open it directly in Obsidian/an editor outside of Claude Code's tools.
- **VALIDATE**: `grep -c '^[A-Z_]*=' .claude/.env.example` returns the same count before and after (no new keys, only comments changed).

### 21. UPDATE `CLAUDE.md` — add Phase 9 section + flip checklist

- **IMPLEMENT**:
  - New section "## Deployment (Phase 9)" placed after "## Security (Phase 8)" and before "## Phase status".
  - Cover: Hetzner CX21 host shape (shared with Lisa, ARM64, 49.13.165.23), `brunoosbrain-*` namespace, deploy artifact tree, key commands (failover, smoke tests, kill switches), single-instance rule (chat = mandatory, others = recommended), the snapshot cold-start failover quirk, OAuth portability, vault git-sync + concat-both, Postgres `DB_BACKEND` switch, log paths.
  - Flip Phase 9 in the checklist from `[ ]` to `[x]` with date.
- **VALIDATE**: `grep -c "^- \[x\] Phase " CLAUDE.md` increments by 1 (8 → 9).

### 22. SMOKE-TEST locally with Postgres on Mac

- **IMPLEMENT**: `brew install postgresql@16 pgvector` (or use Docker — `docker run -d --name brunoosbrain-pg -p 5432:5432 -e POSTGRES_PASSWORD=test pgvector/pgvector:pg16`). Create role+db, run `init.sql`, set `DB_BACKEND=postgres POSTGRES_URL=…`, run `memory_index.py --full`, then `memory_search.py "test query"`. Compare top-5 results vs. SQLite for ≥3 sample queries.
- **VALIDATE**: `cross-backend-smoke.py` reports ≥60% file_path overlap on top-5 for the test queries. RRF ordering will differ slightly between backends — that's expected.

### 23. SEED BRUNO ON HOST + BOOTSTRAP VPS-side

- **IMPLEMENT**:
  1. Run Step 1 / `seed-bruno-on-host.sh` to add Bruno's pubkey to `/home/bruno/.ssh/authorized_keys`.
  2. Add to Mac `~/.ssh/config`:
     ```
     Host brunoos
       HostName 49.13.165.23
       User bruno
       IdentityFile ~/.ssh/id_ed25519
     ```
  3. Verify `ssh brunoos id` works.
  4. Either `ssh brunoos 'bash -s' < deploy/bin/bootstrap-bruno.sh` OR push the repo to a VPS-reachable git remote first and let bootstrap-bruno.sh `git clone` it.
  5. `deploy/bin/sync-secrets.sh`.
  6. `ssh brunoos 'cd /home/bruno/claude-second-brain && /usr/local/bin/uv sync --extra vps'` (in case bootstrap step skipped it).
- **VALIDATE**: `ssh brunoos uname -a` returns `Linux … aarch64 …`; `ssh brunoos 'cd /home/bruno/claude-second-brain && /usr/local/bin/uv run python -c "import psycopg, pgvector; print(\"ok\")"'` returns `ok`.

### 24. VAULT GIT-INIT (Mac, then VPS clone)

- **IMPLEMENT**:
  ```bash
  cd "$BRUNOS_VAULT_PATH"
  git init && git branch -m main
  cp ../deploy/vault/gitignore   .gitignore
  cp ../deploy/vault/gitattributes .gitattributes
  git add -A && git commit -m "init: vault repo"
  gh repo create brunobouwman/brunos-vault --private --source=. --remote=origin --push
  ../deploy/bin/install-merge-driver.sh
  # On VPS:
  ssh brunoos 'cd /home/bruno && git clone git@github.com:brunobouwman/brunos-vault.git BrunOS'
  ssh brunoos 'cd /home/bruno/BrunOS && /home/bruno/claude-second-brain/deploy/bin/install-merge-driver.sh'
  ```
- **GOTCHA**: VPS needs a deploy key for the private repo. Generate `ssh-keygen -f ~/.ssh/brunos-vault-deploy -t ed25519` on the VPS as the bruno user, add the public key to GitHub repo settings → Deploy Keys (read+write). Configure `~/.ssh/config` on VPS to use it for `github.com`.
- **VALIDATE**: `ssh brunoos 'ls /home/bruno/BrunOS/Memory'` returns the expected folders. `ssh brunoos 'cd /home/bruno/BrunOS && git config merge.concat-both.driver'` returns the absolute path.

### 25. POSTGRES INIT + COLD INDEX (VPS)

- **IMPLEMENT**:
  ```bash
  # Generate password on Mac, then push to VPS .env, then init on VPS:
  PG_PW="$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)"
  echo "POSTGRES_PASSWORD=$PG_PW"   # save this — you'll embed it in POSTGRES_URL in .env
  # (then edit .claude/.env on Mac to set POSTGRES_URL=postgresql://brunoosbrain:$PG_PW@localhost:5432/brunoosbrain, scp via sync-secrets.sh)
  ssh brunoos "sudo -u postgres psql -v pw='$PG_PW' -v ON_ERROR_STOP=1 -f /home/bruno/claude-second-brain/deploy/postgres/init.sql"
  ssh brunoos 'cd /home/bruno/claude-second-brain && /usr/local/bin/uv run python .claude/scripts/memory_index.py --full'
  ssh brunoos 'cd /home/bruno/claude-second-brain && /usr/local/bin/uv run python .claude/scripts/memory_search.py "what did I write about Vertik this week"'
  ```
- **GOTCHA**: The password may contain `/` `+` `=` from base64 — strip them (the `tr -d '/+='` above) to keep it URL-safe inside `POSTGRES_URL`. Otherwise URL-encode.
- **VALIDATE**: Cold index completes in ≤2 min for typical vault size; search returns ≥3 results with sane file_paths.

### 26. ENABLE VPS UNITS

- **IMPLEMENT**:
  ```bash
  ssh brunoos 'sudo systemctl daemon-reload'
  ssh brunoos 'sudo systemctl enable --now brunoosbrain-vault-sync.timer brunoosbrain-heartbeat.timer brunoosbrain-reflect.timer brunoosbrain-weekly-review.timer brunoosbrain-news-digest.timer brunoosbrain-slackbot.service'
  ssh brunoos 'systemctl list-timers brunoosbrain-*'
  ssh brunoos 'systemctl status brunoosbrain-slackbot'
  ssh brunoos 'tail -50 /var/log/brunoosbrain/slackbot.log'
  # Also re-confirm Lisa's services are still up:
  ssh brunoos 'systemctl --no-pager list-units --type=service --state=active | grep -E "(brunos|lisas)osbrain"'
  ```
- **VALIDATE**: All timers list with future "next" timestamps; `brunoosbrain-slackbot` is `active (running)` for ≥30 sec without restart loops; Lisa's `lisaosbrain-*` services still listed.

### 27. INSTALL MAC PLISTS (failover-ready)

- **IMPLEMENT**: `bash deploy/bin/install-mac-launchd.sh`. Then load only the git-sync plist: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bruno.brunos.git-sync.plist`.
- **VALIDATE**: `launchctl print gui/$(id -u) | grep brunos` shows git-sync loaded; the other 5 are linked in `~/Library/LaunchAgents/` but NOT loaded.

### 28. END-TO-END SMOKE TEST

- **IMPLEMENT**: From a phone (Mac asleep): DM the Slack bot. Within ~5 sec a single in-thread reply should appear. Mark the time. Wait until the next :00 or :30 BRT — `ssh brunoos 'tail -100 /var/log/brunoosbrain/heartbeat.log'` should show a tick fired and a daily-log entry was written. `cat ~/Documents/claude-second-brain/BrunOS/Memory/daily/$(date +%F).md` on Mac should show that entry within 2 min (post-git-sync cycle).
- **VALIDATE**: All three confirmed.

### 29. UPDATE CLAUDE.md PHASE STATUS

- **IMPLEMENT**: Flip `- [ ] Phase 9` to `- [x] Phase 9 — Deployment (Hetzner CX21 shared host + brunoosbrain systemd + Mac launchd failover + vault git-sync) (YYYY-MM-DD)`.
- **VALIDATE**: `git diff CLAUDE.md` shows only the Phase 9 line + the new "## Deployment (Phase 9)" section.

---

## TESTING STRATEGY

There's no pytest framework wired up in this repo — the project relies on integration smoke tests run from CLI. Phase 9 follows that convention.

### Unit-equivalent (script-internal)

- **Cross-backend Postgres↔SQLite parity**: `deploy/bin/cross-backend-smoke.py` indexes the same vault into both backends, runs ≥5 representative queries (covering each `--path-prefix` folder), reports top-5 file_path overlap. Threshold ≥60%.
- **Merge driver**: bash test that constructs three input files (ancestor, local, remote), runs `git-merge-concat`, asserts the merged output contains every unique line from local and remote.
- **systemd unit syntax**: `systemd-analyze verify deploy/systemd/*.{service,timer}` returns 0.
- **launchd plist syntax**: `plutil -lint deploy/launchd/*.plist` returns 0 for each.

### Integration

- Vault round-trip: append on Mac → wait → confirm on VPS → append on VPS → wait → confirm on Mac.
- Concurrent-write race: append to `Memory/daily/YYYY-MM-DD.md` on both ends within 30s, sync, verify both lines present.
- Slack chat bot single-instance: send DM, expect exactly one reply (count by message timestamps in-thread).
- Heartbeat fires on schedule: `tail -f /var/log/brunoosbrain/heartbeat.log` shows ≥1 invocation in last hour.
- Reflection runs at 08:00 BRT and writes proposed MEMORY.md changes (or a daily-log SUGGESTED block).
- Coexistence: Lisa's `lisaosbrain-*` services remain `active (running)` throughout; her DB role still works (`ssh root@49.13.165.23 'sudo -u postgres psql -d lisaosbrain -c "SELECT 1"'`).

### Edge Cases

- VPS Postgres restart mid-write → `psycopg` exception bubbles up → systemd unit exits non-zero → next timer firing retries (acceptable; no special wiring).
- Vault git-sync conflict on a non-`concat-both` file (e.g. SOUL.md edited from both ends) → standard git conflict markers → manual resolution.
- Mac wakes after 8h sleep, vault git-sync runs on first network packet → may pull a large delta → no special handling.
- Slack workspace token rotation → `brunoosbrain-slackbot.service` exits with auth error → `Restart=on-failure` retries every 10s with exponential backoff. If Slack stays down >5 min, service stays in restart loop — acceptable.
- OAuth refresh token expires (Testing mode, 7d) → Gmail/Calendar reads start returning 401 → heartbeat logs "auth failure" → Bruno re-runs `bootstrap_google_oauth.py` on Mac and re-`scp`s. Long-term fix: switch consent screen to "In Production / Self-Published".
- Memory pressure on shared CX21 (2 vCPU / 4 GB) when both Bruno's and Lisa's heartbeats fire at :00 → stagger Bruno's by +15s in the OnCalendar expression (`*-*-* 08..22:15/30 America/Sao_Paulo`).

---

## VALIDATION COMMANDS

Execute every command. Zero failures = ship.

### Level 1: Syntax & Lint

```bash
# Postgres SQL syntax (requires local postgres; otherwise skip):
psql -h localhost -U brunoosbrain brunoosbrain -f deploy/postgres/init.sql 2>&1 | grep -v ERROR

# systemd units:
systemd-analyze verify deploy/systemd/*.service deploy/systemd/*.timer

# launchd plists:
for f in deploy/launchd/*.plist; do plutil -lint "$f" || exit 1; done

# Bash scripts:
shellcheck deploy/bin/*.sh deploy/bin/git-merge-concat setup.sh || true   # warnings ok, errors not

# Python (Postgres branch import sanity):
DB_BACKEND=sqlite uv run python -c "from db import connect, init_schema; conn = connect(); init_schema(conn); conn.close(); print('sqlite ok')"
DB_BACKEND=postgres POSTGRES_URL=postgresql://localhost/brunoosbrain_test \
  uv run python -c "from db import connect, init_schema; conn = connect(); init_schema(conn); conn.close(); print('postgres ok')"
```

### Level 2: Backend Parity

```bash
DB_BACKEND=sqlite uv run python .claude/scripts/memory_search.py "what did I write about Vertik" --k 5 > /tmp/sqlite.json
DB_BACKEND=postgres POSTGRES_URL=postgresql://localhost/brunoosbrain_test \
  uv run python .claude/scripts/memory_search.py "what did I write about Vertik" --k 5 > /tmp/postgres.json
uv run python deploy/bin/cross-backend-smoke.py /tmp/sqlite.json /tmp/postgres.json   # asserts overlap ≥3/5
```

### Level 3: VPS Bootstrap

```bash
ssh brunoos 'id bruno && /usr/local/bin/uv --version && command -v git-sync'
ssh brunoos 'sudo -u postgres psql -d brunoosbrain -c "\dx vector"'   # vector extension present
ssh brunoos 'cd /home/bruno/claude-second-brain && /usr/local/bin/uv run python -c "import psycopg, pgvector; print(\"ok\")"'
ssh brunoos 'cd /home/bruno/claude-second-brain && /usr/local/bin/uv run python .claude/chat/bot.py --smoke-test'   # exit 0
ssh brunoos 'cd /home/bruno/claude-second-brain && /usr/local/bin/uv run python .claude/scripts/heartbeat.py --dry-run --no-agent'   # exit 0

# Coexistence sanity:
ssh root@49.13.165.23 'systemctl status lisaosbrain-slackbot --no-pager | head -5'   # Lisa's bot still active
ssh root@49.13.165.23 'sudo -u postgres psql -l | grep -E "(lisas|brunos)osbrain"'   # both DBs listed
```

### Level 4: End-to-end

```bash
# Vault round-trip:
echo "- 99:99 sync test from mac" >> "$BRUNOS_VAULT_PATH/Memory/daily/$(date +%F).md"
sleep 240
ssh brunoos "tail -5 /home/bruno/BrunOS/Memory/daily/$(date +%F).md"   # should show the line

# Single-instance bot (manual: DM the bot from a phone, count replies = 1)

# Heartbeat fires:
ssh brunoos 'systemctl list-timers brunoosbrain-heartbeat'   # next run within 30 min
ssh brunoos 'tail -100 /var/log/brunoosbrain/heartbeat.log'   # at least one tick in last 30 min
```

### Level 5: Optional

- Vault corruption canary: `cd BrunOS && git fsck` on both Mac and VPS.
- Postgres backup: `pg_dump brunoosbrain > /tmp/brunoosbrain-$(date +%F).sql` on VPS as a smoke check (don't ship cron yet — Phase 10 candidate).
- Lisa-impact dry-run: ask Lisa to run her standard daily flow (DM her bot, check her dashboard) — confirm Bruno's deploy hasn't touched her surfaces.

---

## ACCEPTANCE CRITERIA

- [ ] `db.py` Postgres branch implements every function in the public API contract; SQLite path is byte-identical pre-vs-post.
- [ ] `pyproject.toml [vps]` adds `pgvector` adapter; `uv sync` (no extras) does NOT install it.
- [ ] All 6 launchd plists pass `plutil -lint`; 5 ship `Disabled=true`, git-sync ships `Disabled=false`.
- [ ] All `brunoosbrain-*` systemd units pass `systemd-analyze verify`.
- [ ] `git-merge-concat` is executable and concatenates lines correctly under the smoke test.
- [ ] `bootstrap-bruno.sh` is idempotent (re-runs are no-ops after first success).
- [ ] `setup.sh` runs cleanly on both Mac (DB_BACKEND=sqlite default) and VPS (DB_BACKEND=postgres).
- [ ] Vault git-init succeeds; `BrunOS/.gitignore` excludes drafts/active and personal/finance.md; `BrunOS/.gitattributes` registers concat-both for daily logs and HABITS.md.
- [ ] VPS Postgres has the `vector` extension installed; `chunks` table created with HNSW + GIN indexes under the `brunoosbrain` role.
- [ ] VPS `memory_search.py` returns sane results post cold-index.
- [ ] All `brunoosbrain-*` systemd timers + `brunoosbrain-slackbot.service` are `enabled` and `active`.
- [ ] Mac plists are linked into `~/Library/LaunchAgents/`; only git-sync is loaded; the others are failover-ready.
- [ ] End-to-end test: Slack DM → single bot reply; heartbeat tick → daily-log entry → visible on Mac within 2 min.
- [ ] CLAUDE.md has a "## Deployment (Phase 9)" section + Phase 9 marked `[x]` in the status checklist.
- [ ] **Coexistence**: Lisa's `lisaosbrain-*` services remained active throughout; her Postgres role + DB untouched; her vault repo untouched.
- [ ] No edits to `.claude/scripts/sanitize.py`, `.claude/hooks/dangerous-bash.py`, `.claude/hooks/block-secrets.py`, or `.claude/settings.json` `PreToolUse` (already shipped in Phase 8).

---

## COMPLETION CHECKLIST

- [ ] All 29 step-by-step tasks completed in order.
- [ ] Each task's `VALIDATE` command passed at the time the task completed.
- [ ] All Level 1–4 validation commands pass (Level 5 optional).
- [ ] No regressions: `DB_BACKEND=sqlite` round-trip on Mac still produces the same memory_search output for ≥3 sample queries.
- [ ] Manual smoke test confirms VPS-hosted bot replies + VPS-hosted heartbeat fires + vault round-trips.
- [ ] CLAUDE.md updated.
- [ ] Lisa pinged + confirmed her services unaffected.
- [ ] PR / commits land in two clean batches: (a) "feat: Phase 9 — Postgres backend + db.py dispatch" (db.py + pyproject + cross-backend smoke), (b) "feat: Phase 9 — Hetzner deploy artifacts + vault git-sync" (everything else).
- [ ] Phase 9 marked done in CLAUDE.md and PRD.

---

## NOTES

**Why Hetzner CX21 ARM64 over the original DigitalOcean US-East**: the host is shared with Lisa, who chose the provider+region. ARM64 wheels exist for every dependency (psycopg-binary, fastembed is pure Python, pgvector apt). Latency to api.anthropic.com is slightly higher from EU than from US-East NYC3, but the practical impact on heartbeat (one synchronous Sonnet call per tick, ≤30 sec budget) is negligible.

**Why Postgres+pgvector instead of staying on SQLite for the VPS too**: Lisa's `lisaosbrain-*` already runs on Postgres on this host. Using the same engine gives Bruno backup/replication parity with Lisa's setup and avoids shipping sqlite-vec on Linux for a one-off use. The abstraction debt is paid down by `db.py`'s dispatch layout.

**Why HNSW over IVFFlat**: at our corpus size (low thousands of chunks), HNSW build time is negligible (<10s), recall is higher, no `lists=100` parameter to tune.

**Why `websearch_to_tsquery` over `plainto_tsquery`**: the former accepts `+required -excluded "phrase" or` operators that the `memory-search` skill already documents for SQLite FTS5. `plainto_tsquery` would silently drop them.

**Why git-sync (simonthum) over alternatives**: 2-min pull/commit/push loop with sane handling of merge driver. Alternatives (Syncthing, rsync) don't preserve git history; cron + `git pull` doesn't auto-commit.

**Why VPS as primary instead of Mac**: 8-hour Mac sleep window. Heartbeat's value comes from "while you're not paying attention" — Mac-primary deploy means heartbeat dies overnight.

**Service-set ambiguity (Bruno's message listed only 4 services)**: Bruno's handover message mentions slackbot + heartbeat + reflect + vault-sync (4 services). CLAUDE.md and the prior plan also include weekly-review and news-digest as scheduled jobs. This plan ships **all 6** by default since they're already in the codebase as scheduled scripts; if Bruno wants the minimum set, drop `brunoosbrain-weekly-review.{service,timer}` and `brunoosbrain-news-digest.{service,timer}` (he can run those manually on Mac or add them later).

**Service-naming exception worth noting**: The launchd plists keep the old `com.bruno.brunos.<svc>.plist` reverse-DNS naming (Apple convention; `brunos` here is just the agent's short name, not the deprecated systemd prefix). The systemd units are the ones that use `brunoosbrain-*` to mirror Lisa. If the reverse-DNS prefix matters for consistency, switch to `com.bruno.brunoosbrain.<svc>.plist` — purely a labelling decision, no functional impact.

**Path discrepancy in Bruno's handover note** (`.claude/scripts/git-me/scripts/git-merge-concat`): this path doesn't exist in the repo and wasn't there in the prior plan either. Likely a corrupted paste from Lisa's setup. This plan keeps the merge driver at `deploy/bin/git-merge-concat` for organizational consistency. If Bruno's actual filesystem layout requires the `git-me` subdirectory, it's a one-line `mv` change to the install-merge-driver.sh path — not a blocker.

**File-form vs. journalctl logging**: Bruno's handover note explicitly asked for `/var/log/brunoosbrain-*.log` files. This plan uses the systemd `LogsDirectory=brunoosbrain` directive plus `StandardOutput=append:/var/log/brunoosbrain/<svc>.log` so logs land at `/var/log/brunoosbrain/<svc>.log` (subfolder, owned by `bruno:bruno` mode 0750 — systemd defaults). Both `tail -f` and `journalctl -u` work. If Bruno strictly wants flat `/var/log/brunoosbrain-<svc>.log` (no subfolder), drop `LogsDirectory=` and pre-create the files via `tmpfiles.d`.

**Confidence Score: 8/10** for one-pass success (up from 7/10 in the previous draft). The bootstrap surface area is dramatically smaller because Lisa already provisioned the host — the highest-risk pieces left are (1) the `db.py` Postgres branch (unchanged risk, mitigated by the cross-backend smoke test) and (2) the vault git-sync setup (well-documented, but the concat-both merge driver has a sharp edge — irreversible if mis-applied to a non-append-only file). Recommend running Step 22 (local Mac Postgres smoke test) end-to-end before doing anything irreversible on the shared VPS, and asking Lisa to spot-check her services after Bruno's units enable.
