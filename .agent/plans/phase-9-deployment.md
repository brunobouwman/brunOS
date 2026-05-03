# Feature: Phase 9 — Deployment (Mac launchd + DigitalOcean VPS + vault git-sync)

The following plan is meant to be executed in **one pass** by a focused agent. It is comprehensive, but you must still validate codebase patterns and external docs before each step. Pay special attention to existing util/type/model names — `db.py`'s public API in particular is contractual: `memory_index.py`, `memory_search.py`, `memory_reflect.py`, and `news-digest`'s dedupe call all import from it, so any rename breaks them.

## Feature Description

Cut over from "Mac-only, manual heartbeat" to a production-grade two-host deployment: a DigitalOcean droplet (Ubuntu 24.04, US-East) hosts the always-on services (heartbeat, reflection, weekly review, news digest, Slack chat bot, vault git-sync). The Mac keeps installed-but-disabled launchd plists for one-command failover. The vault becomes its own git repo (private GitHub) with a custom `concat-both` merge driver so daily logs survive bidirectional sync. The vector index gains a Postgres+pgvector backend so the VPS doesn't ship sqlite-vec (and the SQLite Mac path stays unchanged for local dev).

## User Story

As **Bruno**
I want **BrunOS running 24/7 on a VPS while my MacBook is asleep, with reliable vault sync between both machines**
So that **the heartbeat keeps drafting replies, the chat bot stays online for Slack, daily logs don't corrupt on merge, and I can fail over to local Mac in one command if the VPS is down**.

## Problem Statement

Today every BrunOS service is a manual `uv run python …` invocation on the Mac. The chat bot dies when the laptop closes; the heartbeat never fires unless Bruno triggers it; reflections, weekly reviews, and news digests are entirely manual. There's no DB backend portable to a Linux VPS (sqlite-vec is fine on Mac but Postgres+pgvector is the durable choice for a multi-host deploy). The vault has no git history, so cross-machine sync is impossible.

## Solution Statement

Ship a `deploy/` directory containing launchd plists, systemd units, a `git-merge-concat` driver, an idempotent VPS bootstrap script, and Postgres init SQL. Implement a Postgres branch in `db.py` that mirrors the SQLite public API exactly (so the seven scripts that import it stay untouched). Init the vault as a git repo on Mac, push to a private GitHub repo, clone on VPS, register the merge driver per machine, and schedule git-sync every 2 minutes. VPS is primary; Mac plists install with `Disabled=true` for failover. Single-instance is **mandatory** for the chat bot (Slack Socket Mode is fan-out broadcast — duplicate clients = duplicate replies) and **strongly recommended** for the four scheduled jobs (concat-both makes dual-run safe but doubles SDK cost and risks the local-only `file_lock` race on HABITS.md / MEMORY.md).

## Feature Metadata

**Feature Type**: New Capability (deployment infrastructure)
**Estimated Complexity**: High (Postgres backend port + dual-host orchestration + vault git-sync are each non-trivial)
**Primary Systems Affected**: `db.py` (Postgres branch), new `deploy/` tree, new vault `.gitignore`/`.gitattributes`, `pyproject.toml` (vps extra), `CLAUDE.md` (Phase 9 section)
**Dependencies**: DigitalOcean droplet (US-East, ≥2 vCPU / 4 GB), private GitHub repo `brunobouwman/brunos-vault`, `psycopg[binary]>=3.2`, `pgvector>=0.3` (Python adapter — needs adding to `pyproject.toml [vps]`), Postgres 16 + `postgresql-16-pgvector` apt package, [git-sync](https://github.com/simonthum/git-sync) script

**Decisions confirmed by Bruno (2026-05-03)**:
- Vault remote: private GitHub repo (not Gitea).
- VPS region: US-East (NYC3 chosen — closest to api.anthropic.com).
- VPS Anthropic billing: existing `ANTHROPIC_API_KEY`; provision a new key only if usage spikes.
- Phase 8 timing: run in **parallel**. Phase 8 owns `.claude/scripts/sanitize.py`, `.claude/hooks/dangerous-bash.py`, `.claude/hooks/block-secrets.py`, and the `PreToolUse` block in `.claude/settings.json`. Phase 9 must NOT touch any of those. Both phases land in separate commits and converge in `CLAUDE.md` (each phase appends its own section).

---

## CONTEXT REFERENCES

### Relevant Codebase Files — YOU MUST READ THESE BEFORE IMPLEMENTING

- `.claude/scripts/db.py` (entire file, 178 lines) — Why: defines the **contractual public API** the Postgres branch must mirror exactly: `connect()`, `init_schema(conn)`, `upsert_chunk(conn, file_path, chunk_idx, content, mtime, embedding) -> int`, `delete_chunks_for_file(conn, file_path) -> int`, `vector_search(conn, qemb, k, path_prefix=None) -> list[dict]`, `keyword_search(conn, query, k, path_prefix=None) -> list[dict]`, `all_file_mtimes(conn) -> dict[str, float]`, `get_chunks(conn, ids) -> dict[int, dict]`. Module constant `EMBED_DIM = 384`. Returned row dicts must contain the exact same keys the SQLite path returns (`id, file_path, chunk_idx, content, distance` for vector; `id, file_path, chunk_idx, content, score` for FTS).
- `.claude/scripts/memory_search.py` (entire file, 60 lines) — Why: shows how RRF fusion consumes `vector_search` and `keyword_search`. Only ordinal rank matters (`rank` index in the loop, not the score value itself), so Postgres' `ts_rank_cd` (higher = better) and SQLite's `bm25` (lower = better) both work as long as each backend returns rows already sorted best-first.
- `.claude/scripts/memory_index.py` lines 1-80 — Why: imports `all_file_mtimes`, `connect`, `delete_chunks_for_file`, `init_schema`, `upsert_chunk` from `db`. Confirms the `EXCLUDE_RELATIVE = {"personal/finance.md"}` boundary still applies on Postgres path.
- `.claude/scripts/shared.py` lines 20-87, 240-258 — Why: `REPO_ROOT`, `STATE_DIR`, `BRT` constants you'll reference in deploy artifacts; `load_env()` and `vault_path()` are how every script picks up env vars (the .env path is `REPO_ROOT/.claude/.env`); `_resolve_uv()` shows the uv-binary discovery pattern your systemd `ExecStart` must match (`/home/bruno/.local/bin/uv run …`).
- `.claude/chat/bot.py` lines 1-60 — Why: `CLAUDE_INVOKED_BY=chat` is set BEFORE any SDK import. Your `brunos-chat.service` ExecStart is just `uv run python .claude/chat/bot.py`; the script handles its own env. **Single-instance enforcement target** — see PRD §9.5.
- `.claude/scripts/heartbeat.py` (skim — recently modified, see uncommitted diff) — Why: confirms `_split_chat_bot_handled()` queries Slack API directly, so it works regardless of which machine the bot runs on. Schedule: every 30 min 08:00–22:00 BRT (your `OnCalendar` expression must hit those exact slots).
- `pyproject.toml` lines 30-32 — Why: `[project.optional-dependencies] vps = ["psycopg[binary]>=3.2,<4"]` already exists; you'll add `"pgvector>=0.3,<0.4"` to it.
- `.gitignore` lines 23-27 — Why: `BrunOS/` is gitignored from the code repo, by design. The Phase 9 vault git-init runs **inside** `BrunOS/` and writes a separate `.gitignore` and `.gitattributes` there.
- `.claude/.env.example` (entire file) — Why: documents every env var; `BRUNOS_VAULT_PATH`, `DB_BACKEND`, `POSTGRES_URL`, `ANTHROPIC_API_KEY` (commented note), Google OAuth paths.
- `.agent/plans/second-brain-prd.md` lines 585-686 — Why: canonical PRD for Phase 9. Read before implementing — especially §9.5 single-instance rationale and §9.4 git-sync setup.

### New Files to Create

```
deploy/
  README.md                                  # operator runbook
  bin/
    git-merge-concat                         # merge driver (executable)
    install-merge-driver.sh                  # registers merge.concat-both per machine
    bootstrap-vps.sh                         # idempotent provisioner (run from Mac via ssh)
    sync-secrets.sh                          # scp .env + Google tokens → VPS
    install-mac-launchd.sh                   # symlinks plists into ~/Library/LaunchAgents (Disabled=true)
    rotate-postgres-password.sh              # one-shot helper, generates pw + writes to .env
  launchd/
    com.bruno.brunos.heartbeat.plist
    com.bruno.brunos.reflection.plist
    com.bruno.brunos.weekly-review.plist
    com.bruno.brunos.news-digest.plist
    com.bruno.brunos.chat.plist
    com.bruno.brunos.git-sync.plist
  systemd/
    brunos-heartbeat.service
    brunos-heartbeat.timer
    brunos-reflection.service
    brunos-reflection.timer
    brunos-weekly-review.service
    brunos-weekly-review.timer
    brunos-news-digest.service
    brunos-news-digest.timer
    brunos-chat.service
    brunos-git-sync.service
    brunos-git-sync.timer
  postgres/
    init.sql                                  # role, db, CREATE EXTENSION vector, schema mirror
  vault/
    gitignore                                 # template for BrunOS/.gitignore (cp at init time)
    gitattributes                             # template for BrunOS/.gitattributes
```

Plus modifications:
- `.claude/scripts/db.py` — add Postgres branch (no rename, no public-API change).
- `pyproject.toml` — add `pgvector` to `[vps]`.
- `CLAUDE.md` — append "Phase 9" section + flip checklist.
- `.claude/.env.example` — add comment block for VPS-specific values (no new keys).

### Relevant Documentation — READ BEFORE IMPLEMENTING

- [pgvector Python adapter](https://github.com/pgvector/pgvector-python#psycopg-3) — Why: `register_vector(conn)` registration is mandatory for `np.ndarray` ↔ `vector(384)` adaptation. Use the **synchronous** `psycopg.Connection` path (the codebase is sync; don't mix asyncpg in).
- [pgvector index types](https://github.com/pgvector/pgvector#indexing) — Why: HNSW vs IVFFlat. Pick **HNSW** with `(m=16, ef_construction=64)` defaults — better recall than IVFFlat, no tuning required, and our corpus (≤a few thousand chunks) is small enough that build time isn't a concern.
- [pgvector distance operators](https://github.com/pgvector/pgvector#distances) — Why: `<=>` is cosine distance (lower = better match). Mirror SQLite's `vec0` MATCH operator semantics: return rows ordered ascending by distance.
- [Postgres `tsvector` + `websearch_to_tsquery`](https://www.postgresql.org/docs/16/textsearch-controls.html#TEXTSEARCH-PARSING-QUERIES) — Why: use `websearch_to_tsquery('english', %s)` not `plainto_tsquery` — it accepts the same `+required -excluded "phrase" or` operators the `memory-search` skill documents for SQLite FTS5, so Bruno's escape-hatch queries work on both backends. `ts_rank_cd(fts, query)` for ranking (higher = better → `ORDER BY score DESC`).
- [systemd OnCalendar syntax](https://www.freedesktop.org/software/systemd/man/systemd.time.html#Calendar%20Events) — Why: needed for the heartbeat's "every 30 min between 08:00 and 22:00 BRT". Correct expression: `*-*-* 08..22:00/30:00 America/Sao_Paulo` is **wrong** (not valid syntax); use a list `*-*-* 08,09,10,11,12,13,14,15,16,17,18,19,20,21,22:00/30:00 America/Sao_Paulo` OR two timers, OR — cleanest — `OnCalendar=*-*-* 08..22:00/30 America/Sao_Paulo`. Test with `systemd-analyze calendar "<expr>"` before committing.
- [launchd `StartCalendarInterval` array form](https://www.launchd.info/) — Why: launchd has no "every 30 min between 8 and 22" — you must enumerate ~30 array entries (one per slot). Generate them programmatically inside the plist or in `install-mac-launchd.sh`.
- [git-sync (simonthum)](https://github.com/simonthum/git-sync#description) — Why: the script Bruno installs on both machines. Configure with `git config branch.main.sync true` and `git config branch.main.syncNewFiles true`. Read the README's section "How it handles conflicts" before relying on it.
- [DigitalOcean: Initial server setup Ubuntu 24.04](https://www.digitalocean.com/community/tutorials/initial-server-setup-with-ubuntu-24-04) — Why: standard hardening reference. Adapt: skip the manual user-creation steps the bootstrap script automates.
- [Slack `auth.test` API](https://api.slack.com/methods/auth.test) — Why: smoke-test endpoint for verifying the bot is alive and (implicitly, by counting active Socket Mode connections client-side) running on a single host.

### Patterns to Follow

**Naming Conventions:**
- Python files: `snake_case.py` (project-wide).
- Shell scripts in `deploy/bin/`: `kebab-case` with no extension when invoked directly (e.g. `git-merge-concat`, `bootstrap-vps.sh` — `.sh` extension only on the bootstrap files for grep-ability; the merge driver has no extension because git invokes it by name).
- launchd plists: `com.bruno.brunos.<service>.plist` (reverse-DNS as Apple convention).
- systemd units: `brunos-<service>.service` / `brunos-<service>.timer` (kebab-case, project-prefixed).
- Postgres role and DB: both `brunos` (lower-case, single segment).

**Error Handling (per `db.py` and `shared.py` patterns):**
- DB calls let `psycopg` exceptions propagate. Don't wrap in try/except unless you have a specific recovery path (mirroring `db.keyword_search`'s `OperationalError` swallow on FTS5 parse failure → `[]`; do the same for `psycopg.errors.SyntaxError` from `websearch_to_tsquery`).
- Bash scripts: `set -euo pipefail` at top of every shell file. Use `trap 'echo "FAILED at line $LINENO" >&2' ERR` for diagnostics.
- Bootstrap script idempotency: every step `if`-guarded (e.g. `id bruno >/dev/null 2>&1 || adduser …`).

**Logging Pattern:**
- Python: `_log(msg)` helpers print to `sys.stderr`, never stdout (mirrors `chat/bot.py:44` and `heartbeat.py`). Don't add logging.* — project doesn't use it.
- Shell: `echo "==> <step>"` with `>&2` on bootstrap output.
- systemd: defaults to journalctl; no extra wiring needed. Logs viewable via `journalctl -u brunos-heartbeat -f`.

**Recursion Guard (mandatory for SDK-invoking units):**
- `brunos-chat.service` runs `chat/bot.py` which already sets `CLAUDE_INVOKED_BY=chat` before SDK import. **Don't** set it in the unit file (the script's `os.environ.setdefault` line owns this).
- Same for heartbeat (`heartbeat`), reflection (`reflection`), weekly-review (`weekly-review` — verify in `.claude/skills/weekly-review/scripts/aggregate_week.py`), news-digest (`news-digest` — verify in `.claude/skills/news-digest/scripts/digest.py`).

**`setting_sources` policy** — already enforced inside the scripts. Deploy units don't need to do anything special; they invoke the script and the script sets options correctly.

**Portuguese-vs-English locale:** systemd and launchd unit content stays in English (it's infrastructure). Vault content language routing remains untouched.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation (no VPS access yet)

Build the artifacts that don't depend on the droplet existing.

**Tasks:**
- Author the Postgres branch in `db.py` (this is the largest single piece of code in Phase 9).
- Author every plist, every systemd unit, every helper script in `deploy/`.
- Author `BrunOS/.gitignore` and `BrunOS/.gitattributes` templates under `deploy/vault/`.
- Update `pyproject.toml` (pgvector adapter to `[vps]` extra).
- Update `.claude/.env.example` (VPS-specific notes, no new keys).
- Validate locally:
  - `uv sync` succeeds (no version conflicts from adding pgvector).
  - `DB_BACKEND=sqlite` round-trip still works (no regression).
  - `DB_BACKEND=postgres POSTGRES_URL=postgresql://localhost/brunos_test` against a local Postgres (Mac dev — `brew install postgresql@16 pgvector`) round-trips: index → search → diff results vs. SQLite for the same vault.

### Phase 2: VPS Bootstrap (interactive, with Bruno)

After Bruno provides the droplet IP.

**Tasks:**
- Append SSH config (Host alias `brunos`).
- `ssh root@<ip>` first time to seed the bruno user (the bootstrap script itself can't run before that user exists — the very first command must be manual or use a tiny `root-bootstrap.sh` over `ssh root@<ip>`).
- Run `deploy/bin/bootstrap-vps.sh` end-to-end (idempotent — re-runnable).
- `rsync` the code repo (excluding `.venv`, `BrunOS/`, `.claude/data/`).
- `deploy/bin/sync-secrets.sh` to scp `.claude/.env`, `google_token.json`, `google_client_secrets.json`.
- Edit `/home/bruno/brunos/.claude/.env` on VPS to flip `DB_BACKEND=postgres`, set `POSTGRES_URL`, set `ANTHROPIC_API_KEY`, set `BRUNOS_VAULT_PATH=/home/bruno/BrunOS`.

### Phase 3: Vault Git-Init (Mac side, then VPS clone)

**Tasks:**
- `cd $BRUNOS_VAULT_PATH && git init && git branch -m main`.
- Copy `deploy/vault/gitignore` → `BrunOS/.gitignore`. Same for `gitattributes`.
- Create private GitHub repo `brunobouwman/brunos-vault` (manual, via `gh repo create` or web UI).
- `git remote add origin git@github.com:brunobouwman/brunos-vault.git`.
- `git add -A && git commit -m "init: vault repo" && git push -u origin main`.
- VPS: `cd /home/bruno && git clone git@github.com:brunobouwman/brunos-vault.git BrunOS`.
- Run `deploy/bin/install-merge-driver.sh` on **both** machines (`git config` is per-repo, not committed).
- Smoke-test: append a line to `BrunOS/Memory/daily/$(date +%F).md` on Mac, wait ≤ 4 min, verify it appears on VPS.

### Phase 4: Postgres Init (VPS)

**Tasks:**
- `sudo -u postgres psql -f /home/bruno/brunos/deploy/postgres/init.sql`.
- Cold-build the index: `cd /home/bruno/brunos && uv run python .claude/scripts/memory_index.py --full`.
- Smoke-test query: `uv run python .claude/scripts/memory_search.py "what did I write about Vertik this week"` returns rows that look right.

### Phase 5: systemd Install + Enable (VPS)

**Tasks:**
- Symlink (don't copy — ease of post-deploy edits) every unit from `/home/bruno/brunos/deploy/systemd/` into `/etc/systemd/system/`.
- `sudo systemctl daemon-reload`.
- `sudo systemctl enable --now brunos-git-sync.timer brunos-heartbeat.timer brunos-reflection.timer brunos-weekly-review.timer brunos-news-digest.timer brunos-chat.service`.
- Verify each: `systemctl status brunos-<unit>`, `journalctl -u brunos-<unit> -n 50`.

### Phase 6: Mac Launchd Install (failover-ready, disabled by default)

**Tasks:**
- Run `deploy/bin/install-mac-launchd.sh` — symlinks plists into `~/Library/LaunchAgents/`. Each plist has `Disabled=true` baked in.
- Vault git-sync on Mac is the **one exception** — it loads enabled (it's a read consumer; harmless to dual-run with VPS git-sync).
- Document the failover one-liner in `deploy/README.md`: `defaults write ~/Library/LaunchAgents/com.bruno.brunos.<svc>.plist Disabled -bool false && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bruno.brunos.<svc>.plist`.

### Phase 7: End-to-end Validation + Cutover

**Tasks:**
- Slack DM the bot from a phone (Mac asleep) — verify single reply within seconds.
- Wait for next :00 or :30 BRT — verify VPS heartbeat fires (`journalctl -u brunos-heartbeat -f`) and writes to today's daily log; confirm log appears on Mac via git-sync within 2 min.
- Force a write race on the daily log (manual append on both ends within 30s of each other) → verify both lines survive after sync.
- Update `CLAUDE.md` Phase 9 section + flip phase status to `[x]`.

---

## STEP-BY-STEP TASKS

Execute in order. Each task is atomic and independently testable. Validation commands assume cwd = repo root unless noted.

### 1. UPDATE `pyproject.toml`

- **IMPLEMENT**: Add `pgvector>=0.3,<0.4` to the existing `[vps]` extra (line 32).
- **PATTERN**: Match the trailing-comma style of `[dependencies]`.
- **GOTCHA**: Don't add to top-level dependencies — Mac shouldn't pull pgvector. Phase 9 says it explicitly.
- **VALIDATE**: `uv sync` and `uv sync --extra vps` both succeed; `uv pip list | grep pgvector` only present after `--extra vps`.

### 2. UPDATE `.claude/scripts/db.py` — add Postgres branch

- **IMPLEMENT**:
  - Top-of-file imports: gate `import sqlite_vec` and `import sqlite3` behind `DB_BACKEND` so the VPS doesn't need sqlite-vec installed. Pattern:
    ```python
    BACKEND = os.environ.get("DB_BACKEND", "sqlite")
    if BACKEND == "sqlite":
        import sqlite3
        import sqlite_vec
    elif BACKEND == "postgres":
        import psycopg
        from pgvector.psycopg import register_vector
    ```
  - Replace `connect()` body with a backend dispatch. SQLite path stays unchanged. Postgres path:
    ```python
    url = os.environ["POSTGRES_URL"]
    conn = psycopg.connect(url, autocommit=False, row_factory=psycopg.rows.dict_row)
    register_vector(conn)
    return conn
    ```
  - Add `_PG_SCHEMA` constant mirroring the SQL in `deploy/postgres/init.sql` (so `init_schema(conn)` is a no-op on Postgres in normal operation, but a safety net when running against a fresh DB). On Postgres, `init_schema` runs `CREATE TABLE IF NOT EXISTS …` for files (well, just chunks — there's no separate files table; mtime is per-chunk in SQLite, keep it that way).
  - Add a tiny `_is_postgres(conn)` helper: `isinstance(conn, psycopg.Connection)`.
  - Refactor each query function to dispatch:
    - `upsert_chunk`: SQLite path unchanged. Postgres path uses `INSERT … ON CONFLICT (file_path, chunk_idx) DO UPDATE SET content=EXCLUDED.content, mtime=EXCLUDED.mtime, embedding=EXCLUDED.embedding RETURNING id`. Pass the np.ndarray directly — pgvector's adapter handles the conversion.
    - `delete_chunks_for_file`: Postgres single statement `DELETE FROM chunks WHERE file_path = %s RETURNING id` — count rows.
    - `vector_search`: Postgres `SELECT id, file_path, chunk_idx, content, (embedding <=> %s) AS distance FROM chunks [WHERE file_path LIKE %s || '/%'] ORDER BY embedding <=> %s LIMIT %s`. **Bind the qemb twice** (once for distance, once for ORDER BY) — Postgres doesn't reuse the alias inside ORDER BY in all index plans; binding twice forces the planner to use the HNSW index. Verify with `EXPLAIN`.
    - `keyword_search`: Postgres `SELECT id, file_path, chunk_idx, content, ts_rank_cd(fts, q) AS score FROM chunks, websearch_to_tsquery('english', %s) q WHERE fts @@ q [AND file_path LIKE %s || '/%'] ORDER BY score DESC LIMIT %s`. Wrap in try/except `psycopg.errors.SyntaxError` → return `[]` (mirroring SQLite FTS5's swallow at line 156-158).
    - `all_file_mtimes`: identical SQL, just `%s` placeholder style.
    - `get_chunks`: Postgres `WHERE id = ANY(%s)` with the ids list as a single param.
  - Keep `EMBED_DIM = 384` exported.
- **PATTERN**: Mirror SQLite path's row-dict shape exactly. `psycopg.rows.dict_row` already returns `dict`s — don't wrap. Make sure key names match (`file_path`, `chunk_idx`, `content`, `id`, `distance`, `score`).
- **IMPORTS**: `import psycopg` (sync), `from pgvector.psycopg import register_vector`. Keep these inside the `BACKEND == "postgres"` branch so Mac doesn't need them.
- **GOTCHA #1**: SQLite returns `bm25` (lower=better, `ORDER BY score`); Postgres returns `ts_rank_cd` (higher=better, `ORDER BY score DESC`). RRF only consumes ordinal rank, so this asymmetry is fine — but **don't** change `memory_search.py`'s rrf_fuse code expecting score signs. The score field is informational only.
- **GOTCHA #2**: numpy → pgvector adaptation requires `register_vector(conn)` AFTER `psycopg.connect`. If you forget, you'll get cryptic errors like `adapter not found for class numpy.ndarray`.
- **GOTCHA #3**: `psycopg.connect` defaults to `autocommit=False`. `memory_index.py` calls `conn.commit()` (verify in its source) — if it doesn't, add `conn.commit()` inside `upsert_chunk` after each insert OR set `autocommit=True`. Pick `autocommit=False` and let the indexer commit at end-of-file (matches SQLite semantics — SQLite is implicitly transactional but commits on close).
- **GOTCHA #4**: `LIKE %s || '/%'` — the `%` in `'/%'` is a SQL literal, not a parameter placeholder. psycopg uses `%s` for params and treats `%` in literals fine, but if you ever use `%(named)s`-style params you must double the literal `%` to `%%`. Stick with `%s` to avoid this.
- **VALIDATE**:
  - `DB_BACKEND=sqlite uv run python .claude/scripts/memory_index.py --full --dry-run` (no behavior change).
  - `DB_BACKEND=sqlite uv run python .claude/scripts/memory_search.py "test query"` (returns same shape as before).
  - With local Postgres set up: `DB_BACKEND=postgres POSTGRES_URL=postgresql://localhost/brunos_test uv run python .claude/scripts/memory_index.py --full`, then `… memory_search.py "test query"`. Compare top-5 rows vs. SQLite — file_path overlap should be ≥3/5 for the same query (RRF fusion smooths over backend differences).
  - Diff test: write a 50-line script that runs the same 5 queries against both backends and prints overlap percentage. Commit it under `deploy/bin/cross-backend-smoke.py` for future regression checks.

### 3. CREATE `deploy/postgres/init.sql`

- **IMPLEMENT**:
  ```sql
  -- Run as postgres superuser. Idempotent — safe to re-run.
  CREATE ROLE brunos WITH LOGIN PASSWORD :'pw';   -- pw passed via psql -v pw='...'
  CREATE DATABASE brunos OWNER brunos;
  \c brunos
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
  GRANT ALL ON ALL TABLES IN SCHEMA public TO brunos;
  GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO brunos;
  ```
- **PATTERN**: SQLite schema in `db.py` lines 21-50 (single chunks table, file_path+chunk_idx unique). Mirror this — don't introduce a separate `files` table.
- **GOTCHA**: HNSW index build is fast for our scale (≤a few thousand chunks). Don't tune `m` / `ef_construction` — defaults are fine. Document in init.sql that Bruno can `REINDEX INDEX chunks_embedding_idx` if recall ever degrades.
- **VALIDATE**: `psql -h localhost -U brunos brunos -c "\d chunks"` shows the table with all 4 indexes. `psql -c "SELECT extname FROM pg_extension WHERE extname='vector'"` returns `vector`.

### 4. CREATE `deploy/launchd/com.bruno.brunos.heartbeat.plist`

- **IMPLEMENT**: Standard launchd plist with `Disabled=true`, `Label=com.bruno.brunos.heartbeat`, `ProgramArguments=[<uv-bin>, "run", "python", ".claude/scripts/heartbeat.py"]`, `WorkingDirectory=/Users/brunobouwman/Documents/claude-second-brain`, `EnvironmentVariables={TZ=America/Sao_Paulo, PATH=/Users/brunobouwman/.local/bin:/usr/local/bin:/usr/bin:/bin}`. `StandardErrorPath=/Users/brunobouwman/Documents/claude-second-brain/.claude/data/state/heartbeat.err.log`, `StandardOutPath=/dev/null`. Schedule: `StartCalendarInterval` array of 30 entries (`Hour: 8..22, Minute: 0`) plus 30 entries (`Minute: 30`) — 60 total. Generate the plist programmatically inside `install-mac-launchd.sh` rather than hand-writing 60 dict entries.
- **PATTERN**: Use [launchd plist tutorial](https://www.launchd.info/) as reference.
- **GOTCHA**: launchd ignores `EnvironmentVariables` for `PATH` if the plist is loaded via `launchctl bootstrap` without `--no-passive`; verify your `uv` binary path resolves at runtime (`launchctl print gui/$(id -u)/com.bruno.brunos.heartbeat | grep PATH`).
- **VALIDATE**: `plutil -lint deploy/launchd/com.bruno.brunos.heartbeat.plist` → `OK`. After install: `launchctl print gui/$(id -u)/com.bruno.brunos.heartbeat` shows the unit registered with `state = not running` (because Disabled).

### 5. CREATE the other 5 launchd plists

- **IMPLEMENT**: Apply the heartbeat template to:
  - `com.bruno.brunos.reflection.plist` — runs `.claude/scripts/memory_reflect.py`, daily 08:00 BRT (`StartCalendarInterval={Hour: 8, Minute: 0}`).
  - `com.bruno.brunos.weekly-review.plist` — runs `.claude/skills/weekly-review/scripts/aggregate_week.py`, Sundays 19:00 BRT (`Weekday=0, Hour=19, Minute=0`).
  - `com.bruno.brunos.news-digest.plist` — runs `.claude/skills/news-digest/scripts/digest.py`, daily 07:30 BRT.
  - `com.bruno.brunos.chat.plist` — runs `.claude/chat/bot.py`, `KeepAlive=true`, `RunAtLoad=true`. **Single-instance mandatory** — ships `Disabled=true` like the rest.
  - `com.bruno.brunos.git-sync.plist` — runs `git-sync` (the simonthum script — install path: `/usr/local/bin/git-sync` after `brew install git-sync` or manual install) inside `BrunOS/`, every 2 min (`StartInterval=120`). **This one ships `Disabled=false`** — Mac is a read consumer; harmless to dual-run with VPS git-sync.
- **VALIDATE**: `plutil -lint deploy/launchd/*.plist` all return OK.

### 6. CREATE `deploy/systemd/brunos-heartbeat.service` + `.timer`

- **IMPLEMENT**:
  ```ini
  # brunos-heartbeat.service
  [Unit]
  Description=BrunOS heartbeat
  After=network-online.target

  [Service]
  Type=oneshot
  User=bruno
  Group=bruno
  WorkingDirectory=/home/bruno/brunos
  EnvironmentFile=/home/bruno/brunos/.claude/.env
  Environment=TZ=America/Sao_Paulo
  Environment=PATH=/home/bruno/.local/bin:/usr/local/bin:/usr/bin:/bin
  ExecStart=/home/bruno/.local/bin/uv run python .claude/scripts/heartbeat.py
  TimeoutStartSec=300
  ```
  ```ini
  # brunos-heartbeat.timer
  [Unit]
  Description=BrunOS heartbeat — every 30 min between 08:00 and 22:00 BRT

  [Timer]
  OnCalendar=*-*-* 08..22:00/30 America/Sao_Paulo
  Persistent=false
  Unit=brunos-heartbeat.service

  [Install]
  WantedBy=timers.target
  ```
- **PATTERN**: Read [systemd.timer man](https://www.freedesktop.org/software/systemd/man/systemd.timer.html) and verify `Persistent=false` (we don't want missed ticks to fire on boot — they're 30-min cadence; one missed tick is fine).
- **GOTCHA**: `OnCalendar` with `America/Sao_Paulo` requires systemd 244+ (Ubuntu 24.04 ships 255 — fine). Validate the expression with `systemd-analyze calendar "*-*-* 08..22:00/30 America/Sao_Paulo"` — output must show next run within 30 min during BRT business hours.
- **VALIDATE**: `systemd-analyze verify deploy/systemd/brunos-heartbeat.{service,timer}` → no errors.

### 7. CREATE the other 4 timers + `brunos-chat.service` + `brunos-git-sync` pair

- **IMPLEMENT**: Apply the heartbeat template:
  - `brunos-reflection.timer`: `OnCalendar=*-*-* 08:00 America/Sao_Paulo`. Service runs `.claude/scripts/memory_reflect.py`.
  - `brunos-weekly-review.timer`: `OnCalendar=Sun *-*-* 19:00 America/Sao_Paulo`. Service runs `.claude/skills/weekly-review/scripts/aggregate_week.py`.
  - `brunos-news-digest.timer`: `OnCalendar=*-*-* 07:30 America/Sao_Paulo`. Service runs `.claude/skills/news-digest/scripts/digest.py`.
  - `brunos-chat.service` (no timer, long-running):
    ```ini
    [Service]
    Type=simple
    Restart=on-failure
    RestartSec=10
    User=bruno
    WorkingDirectory=/home/bruno/brunos
    EnvironmentFile=/home/bruno/brunos/.claude/.env
    Environment=TZ=America/Sao_Paulo
    Environment=PATH=/home/bruno/.local/bin:/usr/local/bin:/usr/bin:/bin
    ExecStart=/home/bruno/.local/bin/uv run python .claude/chat/bot.py
    [Install]
    WantedBy=multi-user.target
    ```
  - `brunos-git-sync.{service,timer}`: timer `OnCalendar=*:0/2` (every 2 min). Service `Type=oneshot`, `WorkingDirectory=/home/bruno/BrunOS`, `ExecStart=/usr/local/bin/git-sync` (or wherever the script lands).
- **VALIDATE**: `systemd-analyze verify deploy/systemd/*.{service,timer}` → no errors for any.

### 8. CREATE `deploy/bin/git-merge-concat`

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
- **GOTCHA**: This driver intentionally drops line ORDER (it sorts to compute the diff). For `Memory/daily/*.md` and `Memory/HABITS.md` that's acceptable — they're chronological appends and Bruno reads them top-to-bottom-by-timestamp anyway. Document this in `deploy/README.md` so future Bruno doesn't get surprised.
- **VALIDATE**: `bash deploy/bin/git-merge-concat <(echo a) <(printf 'a\nb\n') <(printf 'a\nc\n') /dev/null` produces `a`, `b`, `c` (one per line, possibly reordered) on stdout via `cat $LOCAL` afterward.

### 9. CREATE `deploy/bin/install-merge-driver.sh`

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
- **VALIDATE**: After running inside `BrunOS/` on Mac: `git config merge.concat-both.driver` returns the expected absolute path.

### 10. CREATE `deploy/vault/gitignore` and `deploy/vault/gitattributes`

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

### 11. CREATE `deploy/bin/bootstrap-vps.sh`

- **IMPLEMENT**: Idempotent script meant to be `ssh root@<ip> bash -s < bootstrap-vps.sh` (first run as root) and `ssh brunos bash deploy/bin/bootstrap-vps.sh` (subsequent runs as bruno).
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  trap 'echo "FAILED at line $LINENO" >&2' ERR

  echo "==> ensure user bruno exists"
  if ! id bruno >/dev/null 2>&1; then
    adduser --disabled-password --gecos "" bruno
    usermod -aG sudo bruno
    mkdir -p /home/bruno/.ssh
    cp /root/.ssh/authorized_keys /home/bruno/.ssh/authorized_keys
    chown -R bruno:bruno /home/bruno/.ssh
    chmod 700 /home/bruno/.ssh && chmod 600 /home/bruno/.ssh/authorized_keys
    echo "bruno ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/bruno
    chmod 440 /etc/sudoers.d/bruno
  fi

  echo "==> harden sshd"
  sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/'  /etc/ssh/sshd_config
  sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
  systemctl reload ssh

  echo "==> ufw"
  ufw --force reset
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow 22/tcp
  ufw --force enable

  echo "==> apt"
  apt update
  apt install -y ca-certificates curl git build-essential rsync \
    postgresql-16 postgresql-16-pgvector \
    python3.13 python3.13-venv python3.13-dev

  echo "==> uv (per-user)"
  if ! sudo -u bruno test -x /home/bruno/.local/bin/uv; then
    sudo -u bruno bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
  fi

  echo "==> git-sync"
  if ! command -v git-sync >/dev/null; then
    curl -fsSL https://raw.githubusercontent.com/simonthum/git-sync/master/git-sync \
      -o /usr/local/bin/git-sync
    chmod +x /usr/local/bin/git-sync
  fi

  echo "==> postgres role + db"
  sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='brunos'" | grep -q 1 || \
    sudo -u postgres psql -v pw="${POSTGRES_PASSWORD}" -f /home/bruno/brunos/deploy/postgres/init.sql

  echo "==> systemd units (symlinks)"
  for unit in /home/bruno/brunos/deploy/systemd/*.{service,timer}; do
    [ -f "$unit" ] || continue
    ln -sf "$unit" "/etc/systemd/system/$(basename "$unit")"
  done
  systemctl daemon-reload

  echo "==> done. Next: enable units once secrets + vault are in place."
  ```
- **GOTCHA #1**: `POSTGRES_PASSWORD` env var must be exported before running the bootstrap. The runbook in `deploy/README.md` instructs Bruno (or the agent) to generate it via `openssl rand -base64 24`.
- **GOTCHA #2**: `postgresql-16-pgvector` is the package name on Ubuntu 24.04. If apt can't find it, fall back to building from source per pgvector docs (rare on 24.04 LTS).
- **GOTCHA #3**: The script uses `sudo -u postgres psql -f`, but `init.sql` references `:'pw'` — that variable comes from `psql -v pw='...'`. The current call passes `-v pw="${POSTGRES_PASSWORD}"` correctly. **Don't** put the password in the SQL file.
- **VALIDATE**: After running on a fresh droplet: `id bruno` succeeds, `sudo -u bruno /home/bruno/.local/bin/uv --version` succeeds, `sudo -u postgres psql -d brunos -c "\dx vector"` shows the extension.

### 12. CREATE `deploy/bin/sync-secrets.sh`

- **IMPLEMENT**:
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  : "${VPS_HOST:=brunos}"
  REPO="$(cd "$(dirname "$0")/../.." && pwd)"
  scp "$REPO/.claude/.env" "$VPS_HOST:/home/bruno/brunos/.claude/.env"
  scp "$REPO/.claude/data/state/google_token.json" "$VPS_HOST:/home/bruno/brunos/.claude/data/state/"
  scp "$REPO/.claude/data/state/google_client_secrets.json" "$VPS_HOST:/home/bruno/brunos/.claude/data/state/"
  ssh "$VPS_HOST" "chmod 600 /home/bruno/brunos/.claude/.env"
  echo "==> remember to edit /home/bruno/brunos/.claude/.env on VPS:"
  echo "      BRUNOS_VAULT_PATH=/home/bruno/BrunOS"
  echo "      DB_BACKEND=postgres"
  echo "      POSTGRES_URL=postgresql://brunos:<pw>@localhost:5432/brunos"
  echo "      ANTHROPIC_API_KEY=<your key>"
  ```
- **GOTCHA**: This is the **only** point in the deploy where secrets transit the wire. Use scp (encrypted), not rsync over plain ssh. Don't commit the resulting `.env` anywhere.
- **VALIDATE**: After running, `ssh brunos cat /home/bruno/brunos/.claude/.env | head -5` shows the env, and the file is mode 600.

### 13. CREATE `deploy/bin/install-mac-launchd.sh`

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
    # Default: Disabled=true. The git-sync plist overrides to Disabled=false in its source.
    plutil -lint "$plist" >/dev/null
  done
  echo "==> linked $(ls "$REPO/deploy/launchd" | wc -l | tr -d ' ') plists into $TARGET"
  echo "==> to ENABLE a unit (e.g. failover): launchctl bootstrap gui/$(id -u) $TARGET/com.bruno.brunos.<svc>.plist"
  echo "==> to LOAD vault git-sync (recommended now): launchctl bootstrap gui/$(id -u) $TARGET/com.bruno.brunos.git-sync.plist"
  ```
- **VALIDATE**: After running, `ls -la ~/Library/LaunchAgents/com.bruno.brunos.*` shows symlinks; `launchctl print gui/$(id -u)` doesn't list them yet (they're not loaded).

### 14. CREATE `deploy/bin/rotate-postgres-password.sh`

- **IMPLEMENT**: Helper that generates a 24-byte base64 password, writes it to `.claude/.env` (replacing existing `POSTGRES_URL` if present), and runs `ALTER ROLE brunos WITH PASSWORD '...'` on the VPS via ssh. Idempotent.
- **GOTCHA**: `.claude/.env` is gitignored — but back it up via `cp` before sed-replacing in case the sed regex misfires.
- **VALIDATE**: After running, `psql "$POSTGRES_URL" -c 'SELECT 1'` succeeds with the new password.

### 15. CREATE `deploy/README.md`

- **IMPLEMENT**: Operator runbook covering:
  - First-time deploy sequence (steps 1–7 in this plan, mapped to commands).
  - Failover Mac→VPS: `defaults write … Disabled -bool false && launchctl bootstrap …` for each unit; reverse to fail back.
  - Single-instance verification: `slack auth.test` polled from both machines should show only one bot's `bot_id` actively connected (Slack doesn't expose this directly — check by sending a DM and counting replies).
  - Concat-both merge driver caveats (drops line order in the merged section — fine for daily logs, not for everything).
  - Snapshot cold-start behaviour on failover (heartbeat's first tick will treat everything as new).
  - OAuth refresh-token expiry (7d if consent screen is in Testing — instruct Bruno to flip to "In Production" / "Self-Published").
  - Where logs live (`journalctl -u brunos-<svc>` on VPS; `~/Library/Logs/com.bruno.brunos.*` on Mac via plist `StandardErrorPath`).
- **VALIDATE**: `wc -l deploy/README.md` ≥ 100 lines; cross-reference with each `deploy/bin/*` script.

### 16. UPDATE `.claude/.env.example`

- **IMPLEMENT**: Add a comment block above `BRUNOS_VAULT_PATH` explaining the Mac-vs-VPS split (already partially there). Add a comment above `DB_BACKEND` explicitly noting "set to `postgres` on VPS, `sqlite` on Mac". Add a comment above the (still commented) `ANTHROPIC_API_KEY=` line saying "MUST be set on VPS — Claude Max OAuth doesn't auto-discover headlessly".
- **GOTCHA**: Do NOT commit any actual secret value. Only comments and key=empty lines.
- **VALIDATE**: `grep -c '^[A-Z_]*=' .claude/.env.example` returns the same count before and after (no new keys, only comments changed).

### 17. UPDATE `CLAUDE.md` — add Phase 9 section + flip checklist

- **IMPLEMENT**:
  - New section "## Deployment (Phase 9)" placed after "## Slack chat bot (Phase 7)" and before "## Phase status".
  - Cover: VPS host shape, deploy artifact tree, key commands (failover, smoke tests, kill switches), single-instance rule (chat = mandatory, others = recommended), the snapshot cold-start failover quirk, OAuth portability, vault git-sync + concat-both, Postgres `DB_BACKEND` switch.
  - Flip Phase 9 in the checklist from `[ ]` to `[x]` with date.
- **GOTCHA**: Phase 8 is landing in parallel and also touches CLAUDE.md. Coordinate via separate sections (no overlap) and do a final merge check before commit. **Don't** edit Phase 8's section even if it's incomplete in your branch.
- **VALIDATE**: `grep -c "^- \[x\] Phase " CLAUDE.md` increments by 1 (from 8 to 9 after Phase 8 also lands; you only own +1 of those).

### 18. SMOKE-TEST locally with Postgres on Mac

- **IMPLEMENT**: `brew install postgresql@16 pgvector` (or use a Docker container — `docker run -d --name brunos-pg -p 5432:5432 -e POSTGRES_PASSWORD=test pgvector/pgvector:pg16`). Create role+db, run `init.sql`, set `DB_BACKEND=postgres POSTGRES_URL=…`, run `memory_index.py --full`, then `memory_search.py "test query"`. Compare top-5 results vs. SQLite for ≥3 sample queries.
- **VALIDATE**: Cross-backend overlap script (deploy/bin/cross-backend-smoke.py) reports ≥60% file_path overlap on top-5 for the test queries. RRF ordering will differ slightly between backends — that's expected; we want approximate parity, not byte-identity.

### 19. VPS PROVISIONING (interactive — needs IP)

- **IMPLEMENT**: Once Bruno provides droplet IP:
  1. Append SSH config Host alias `brunos` → `~/.ssh/config`.
  2. `ssh-copy-id -i ~/.ssh/brunos_vps_ed25519.pub root@<ip>` if key wasn't seeded at droplet creation.
  3. `ssh root@<ip> 'bash -s' < deploy/bin/bootstrap-vps.sh` (with `POSTGRES_PASSWORD` exported).
  4. Update SSH config to `User bruno`.
  5. `rsync -av --exclude='.venv' --exclude='BrunOS' --exclude='.claude/data/state' --exclude='.claude/data/fastembed_cache' --exclude='__pycache__' ./ brunos:/home/bruno/brunos/`.
  6. `ssh brunos 'mkdir -p /home/bruno/brunos/.claude/data/state'` (sync excluded it).
  7. `deploy/bin/sync-secrets.sh`.
  8. `ssh brunos 'cd /home/bruno/brunos && /home/bruno/.local/bin/uv sync --extra vps'`.
- **VALIDATE**: `ssh brunos uname -a` returns Linux; `ssh brunos 'cd /home/bruno/brunos && /home/bruno/.local/bin/uv run python -c "import psycopg, pgvector; print(\"ok\")"'` returns `ok`.

### 20. VAULT GIT-INIT (Mac, then VPS clone)

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
  ssh brunos 'cd /home/bruno && git clone git@github.com:brunobouwman/brunos-vault.git BrunOS'
  ssh brunos 'cd /home/bruno/BrunOS && /home/bruno/brunos/deploy/bin/install-merge-driver.sh'
  ```
- **GOTCHA**: VPS needs a deploy key for the private repo. Generate `ssh-keygen -f ~/.ssh/brunos-vault-deploy -t ed25519` on the VPS, add the public key to GitHub repo settings → Deploy Keys (read+write).
- **VALIDATE**: `ssh brunos 'ls /home/bruno/BrunOS/Memory'` returns the expected folders. `ssh brunos 'cd /home/bruno/BrunOS && git config merge.concat-both.driver'` returns the absolute path.

### 21. POSTGRES INIT + COLD INDEX (VPS)

- **IMPLEMENT**:
  ```bash
  ssh brunos 'sudo -u postgres psql -v pw="$(grep ^POSTGRES_PASSWORD /home/bruno/brunos/.claude/.env | cut -d= -f2)" -f /home/bruno/brunos/deploy/postgres/init.sql'
  ssh brunos 'cd /home/bruno/brunos && /home/bruno/.local/bin/uv run python .claude/scripts/memory_index.py --full'
  ssh brunos 'cd /home/bruno/brunos && /home/bruno/.local/bin/uv run python .claude/scripts/memory_search.py "what did I write about Vertik this week"'
  ```
- **VALIDATE**: Cold index completes in ≤2 min for typical vault size; search returns ≥3 results with sane file_paths.

### 22. ENABLE VPS UNITS

- **IMPLEMENT**:
  ```bash
  ssh brunos 'sudo systemctl daemon-reload'
  ssh brunos 'sudo systemctl enable --now brunos-git-sync.timer brunos-heartbeat.timer brunos-reflection.timer brunos-weekly-review.timer brunos-news-digest.timer brunos-chat.service'
  ssh brunos 'systemctl list-timers brunos-*'
  ssh brunos 'systemctl status brunos-chat'
  ```
- **VALIDATE**: All timers list with future "next" timestamps; `brunos-chat` is `active (running)` for ≥30 sec without restart loops.

### 23. INSTALL MAC PLISTS (failover-ready)

- **IMPLEMENT**: `bash deploy/bin/install-mac-launchd.sh`. Then load only the git-sync plist: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bruno.brunos.git-sync.plist`.
- **VALIDATE**: `launchctl print gui/$(id -u) | grep brunos` shows git-sync loaded; the other 5 are linked in `~/Library/LaunchAgents/` but NOT loaded.

### 24. END-TO-END SMOKE TEST

- **IMPLEMENT**: From a phone (Mac asleep): DM the Slack bot. Within ~5 sec a single in-thread reply should appear. Mark the time. Wait until the next :00 or :30 BRT — `journalctl -u brunos-heartbeat -n 100` on VPS should show a tick fired and a daily-log entry was written. `cat ~/.../BrunOS/Memory/daily/$(date +%F).md` on Mac should show that entry within 2 min (post-git-sync cycle).
- **VALIDATE**: All three confirmed.

### 25. UPDATE CLAUDE.md PHASE STATUS

- **IMPLEMENT**: Flip `- [ ] Phase 9` to `- [x] Phase 9 — Deployment (Mac launchd + VPS systemd + vault git-sync) (YYYY-MM-DD)`.
- **VALIDATE**: `git diff CLAUDE.md` shows only the Phase 9 line + the new "## Deployment (Phase 9)" section. No accidental Phase 8 edits.

---

## TESTING STRATEGY

There's no pytest framework wired up in this repo — the project relies on integration smoke tests run from CLI. Phase 9 follows that convention.

### Unit-equivalent (script-internal)

- **Cross-backend Postgres↔SQLite parity**: `deploy/bin/cross-backend-smoke.py` indexes the same vault into both backends, runs ≥5 representative queries (covering each `--path-prefix` folder), reports top-5 file_path overlap. Threshold ≥60% — RRF on different candidate pools won't be byte-identical, but should be substantially overlapping.
- **Merge driver**: bash test that constructs three input files (ancestor, local, remote), runs `git-merge-concat`, asserts the merged output contains every unique line from local and remote.
- **systemd unit syntax**: `systemd-analyze verify deploy/systemd/*.{service,timer}` returns 0.
- **launchd plist syntax**: `plutil -lint deploy/launchd/*.plist` returns 0 for each.

### Integration

- Vault round-trip: append on Mac → wait → confirm on VPS → append on VPS → wait → confirm on Mac.
- Concurrent-write race: append to `Memory/daily/YYYY-MM-DD.md` on both ends within 30s, sync, verify both lines present.
- Slack chat bot single-instance: send DM, expect exactly one reply (count by message timestamps in-thread).
- Heartbeat fires on schedule: `journalctl -u brunos-heartbeat --since "1 hour ago"` shows ≥1 invocation.
- Reflection runs at 08:00 BRT and writes proposed MEMORY.md changes (or a daily-log SUGGESTED block).

### Edge Cases

- VPS Postgres restart mid-write → `psycopg` exception bubbles up → systemd unit exits non-zero → next timer firing retries (this is acceptable; no special wiring needed).
- Vault git-sync conflict on a non-`concat-both` file (e.g. SOUL.md edited from both ends) → Bruno gets standard git conflict markers in the file → manual resolution. Documented in `deploy/README.md`.
- Mac wakes after 8h sleep, vault git-sync runs on first network packet → may pull a large delta → no special handling needed (git-sync handles it).
- Slack workspace token rotation → `brunos-chat.service` exits with auth error → systemd `Restart=on-failure` retries every 10s with exponential backoff (default `RestartSec=10`, no `StartLimitBurst` set; if Slack stays down longer than 5 min, the service stays in restart loop — acceptable).
- OAuth refresh token expires (Testing mode, 7d) → Gmail/Calendar reads start returning 401 → heartbeat logs "auth failure" → Bruno re-runs `bootstrap_google_oauth.py` on Mac and re-`scp`s. **Long-term fix**: switch consent screen to "In Production / Self-Published" — documented in `deploy/README.md`.

---

## VALIDATION COMMANDS

Execute every command. Zero failures = ship.

### Level 1: Syntax & Lint

```bash
# Postgres SQL syntax (requires local postgres; otherwise skip):
psql -h localhost -U brunos brunos -f deploy/postgres/init.sql --dry-run 2>&1 | grep -v ERROR

# systemd units:
systemd-analyze verify deploy/systemd/*.service deploy/systemd/*.timer

# launchd plists:
for f in deploy/launchd/*.plist; do plutil -lint "$f" || exit 1; done

# Bash scripts:
shellcheck deploy/bin/*.sh deploy/bin/git-merge-concat || true   # warnings ok, errors not

# Python (Postgres branch):
DB_BACKEND=sqlite uv run python -c "from .claude.scripts.db import connect, init_schema; conn = connect(); init_schema(conn); conn.close(); print('sqlite ok')"
DB_BACKEND=postgres POSTGRES_URL=postgresql://localhost/brunos_test \
  uv run python -c "from .claude.scripts.db import connect, init_schema; conn = connect(); init_schema(conn); conn.close(); print('postgres ok')"
```

### Level 2: Backend Parity

```bash
DB_BACKEND=sqlite uv run python .claude/scripts/memory_search.py "what did I write about Vertik" --k 5 > /tmp/sqlite.json
DB_BACKEND=postgres POSTGRES_URL=postgresql://localhost/brunos_test \
  uv run python .claude/scripts/memory_search.py "what did I write about Vertik" --k 5 > /tmp/postgres.json
uv run python deploy/bin/cross-backend-smoke.py /tmp/sqlite.json /tmp/postgres.json   # asserts overlap ≥3/5
```

### Level 3: VPS Bootstrap

```bash
# Smoke tests on the droplet:
ssh brunos 'id bruno && /home/bruno/.local/bin/uv --version && command -v git-sync'
ssh brunos 'sudo -u postgres psql -d brunos -c "\dx vector"'   # vector extension present
ssh brunos 'cd /home/bruno/brunos && /home/bruno/.local/bin/uv run python -c "import psycopg, pgvector; print(\"ok\")"'
ssh brunos 'cd /home/bruno/brunos && /home/bruno/.local/bin/uv run python .claude/chat/bot.py --smoke-test'   # exit 0
ssh brunos 'cd /home/bruno/brunos && /home/bruno/.local/bin/uv run python .claude/scripts/heartbeat.py --dry-run --no-agent'   # exit 0
```

### Level 4: End-to-end

```bash
# Vault round-trip:
echo "- 99:99 sync test from mac" >> "$BRUNOS_VAULT_PATH/Memory/daily/$(date +%F).md"
sleep 240
ssh brunos "tail -5 /home/bruno/BrunOS/Memory/daily/$(date +%F).md"   # should show the line

# Single-instance bot (manual: DM the bot from a phone, count replies = 1)

# Heartbeat fires:
ssh brunos 'systemctl list-timers brunos-heartbeat'   # next run within 30 min
ssh brunos 'journalctl -u brunos-heartbeat --since "1 hour ago" | grep "stage 1"'   # at least one tick
```

### Level 5: Optional

- Vault corruption canary: `cd BrunOS && git fsck` on both Mac and VPS.
- Postgres backup: `pg_dump brunos > /tmp/brunos-$(date +%F).sql` on VPS as a smoke check (don't ship cron yet — Phase 10 candidate).

---

## ACCEPTANCE CRITERIA

- [ ] `db.py` Postgres branch implements every function in the public API contract; SQLite path is byte-identical pre-vs-post.
- [ ] `pyproject.toml [vps]` adds `pgvector` adapter; `uv sync` (no extras) does NOT install it.
- [ ] All 6 launchd plists pass `plutil -lint`; 5 ship `Disabled=true`, git-sync ships `Disabled=false`.
- [ ] All 7 systemd units pass `systemd-analyze verify`.
- [ ] `git-merge-concat` is executable and concatenates lines correctly under the smoke test.
- [ ] `bootstrap-vps.sh` is idempotent (re-runs are no-ops after first success).
- [ ] Vault git-init succeeds; `BrunOS/.gitignore` excludes drafts/active and personal/finance.md; `BrunOS/.gitattributes` registers concat-both for daily logs and HABITS.md.
- [ ] VPS Postgres has the `vector` extension installed; `chunks` table created with HNSW + GIN indexes.
- [ ] VPS `memory_search.py` returns sane results post cold-index.
- [ ] All systemd timers + `brunos-chat.service` are `enabled` and `active`.
- [ ] Mac plists are linked into `~/Library/LaunchAgents/`; only git-sync is loaded; the others are failover-ready.
- [ ] End-to-end test: Slack DM → single bot reply; heartbeat tick → daily-log entry → visible on Mac within 2 min.
- [ ] CLAUDE.md has a "## Deployment (Phase 9)" section + Phase 9 marked `[x]` in the status checklist.
- [ ] No edits to `.claude/scripts/sanitize.py`, `.claude/hooks/dangerous-bash.py`, `.claude/hooks/block-secrets.py`, or `.claude/settings.json` `PreToolUse` (those belong to Phase 8).

---

## COMPLETION CHECKLIST

- [ ] All 25 step-by-step tasks completed in order.
- [ ] Each task's `VALIDATE` command passed at the time the task completed.
- [ ] All Level 1–4 validation commands pass (Level 5 optional).
- [ ] No regressions: `DB_BACKEND=sqlite` round-trip on Mac still produces the same memory_search output for ≥3 sample queries.
- [ ] Manual smoke test confirms VPS-hosted bot replies + VPS-hosted heartbeat fires + vault round-trips.
- [ ] CLAUDE.md updated.
- [ ] PR / commits land in two clean batches: (a) "feat: Phase 9 — Postgres backend + db.py dispatch" (db.py + pyproject + cross-backend smoke), (b) "feat: Phase 9 — VPS deploy artifacts + vault git-sync" (everything else). Phase 8's commits land separately on the parallel branch.
- [ ] Phase 9 marked done in CLAUDE.md and PRD.

---

## NOTES

**Why Postgres+pgvector instead of staying on SQLite for the VPS too**: SQLite + sqlite-vec works fine on Linux. The reason for Postgres is forward-compatibility — if Bruno later adds a second VPS, multi-host concurrent writes break sqlite-vec (single-writer only) but Postgres handles them. Phase 9 doesn't ship that yet, but the abstraction debt is already paid down by `db.py`'s dispatch layout, so this is the right time to move.

**Why HNSW over IVFFlat**: at our corpus size (low thousands of chunks), HNSW build time is negligible (<10s), recall is higher, and there's no `lists=100` parameter to tune. IVFFlat would be the right call at >100k chunks; we're nowhere near.

**Why `websearch_to_tsquery` over `plainto_tsquery`**: the former accepts `+required -excluded "phrase" or` operators that the `memory-search` skill already documents for SQLite FTS5. Bruno's queries use these. `plainto_tsquery` would silently drop them.

**Why git-sync (simonthum) over alternatives**: Phase 9 needs a 2-min pull/commit/push loop with sane handling of merge driver. Alternatives (Syncthing, rsync) don't preserve git history; cron + `git pull` doesn't auto-commit. simonthum/git-sync is purpose-built for this exact use case.

**Why VPS as primary instead of Mac**: the 8-hour Mac sleep window is the actual driver. Heartbeat's value comes from "while you're not paying attention" — a Mac-primary deploy means heartbeat dies overnight. VPS-primary keeps it on; Mac becomes the failover for VPS outages.

**Confidence Score: 7/10** for one-pass success. The Postgres branch is the highest-risk piece — small mismatches in row-dict shape or `register_vector` placement will surface as opaque integration-test failures. The VPS bootstrap is straightforward apt/sed scripting. The vault git-sync is well-documented but has a sharp edge (concat-both drops line order — irreversible if Bruno tries to use it on a non-append-only file). Recommend a real-Postgres local Mac smoke test (Step 18) before doing anything irreversible on the VPS.
