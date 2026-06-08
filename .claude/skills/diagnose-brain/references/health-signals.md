# Health Signals — every component + how to verify it

> The exhaustive parts list. Each row: path · purpose · CLI · `CLAUDE_INVOKED_BY` · state
> files · **health signal** (the non-destructive assertion that verifies it's present AND
> working). The *health signal* is what you run against the target brain for each
> diagnosis-checklist check.
>
> **Synced from** the BrunOS vault: `Memory/projects/Brain/architecture/02-component-inventory.md`.
> Trust observed code over this doc if they drift.

~43 Python modules across `.claude/scripts/`, `.claude/hooks/`, `.claude/chat/`.

---

## 1 · Memory / RAG

| Path | Purpose | CLI / flags | State | Health signal |
|---|---|---|---|---|
| `scripts/memory_index.py` | Incremental vault chunker (400-tok/50-overlap); deletion detection; wikilink→edges | `[--full] [--paths …] [--dry-run]` | `memory.db` (rw) | exit 0 + "indexed N files"; DB has `chunks` + `edges` |
| `scripts/memory_search.py` | Hybrid retrieval: vector + FTS5 → RRF → graph aug → pending buffer | `<query> [--k N] [--path-prefix F] [--no-graph] [--no-pending]` | `memory.db` (r), `personal_pending.json` (r) | exit 0, JSON stdout, ≥1 hit on canary |
| `scripts/embeddings.py` | FastEmbed singleton (BGE-small-en-v1.5, 384-dim) | module | `fastembed_cache/` (rw) | import OK; `embed_passages`/`embed_query` callable |
| `scripts/db.py` | SQLite + sqlite-vec + FTS5 backend; edges | module | `memory.db` (rw) | `connect()` live; `PRAGMA quick_check` passes; `chunks`+`chunk_vec`+`chunk_fts`+`edges` exist |

## 2 · Reflection / Dreaming

| Path | Purpose | CLI / flags | `CLAUDE_INVOKED_BY` | State | Health signal |
|---|---|---|---|---|---|
| `scripts/memory_reflect.py` | 3-stage: daily distill → inbox pass (strip+scrub+clear) → curation (drain→MEMORY.md, evict) | `[--inbox-only] [--skip-inbox] [--curate-only] [--dry-run] [--project S]` | `reflection` | `last_reflection.json`, `inbox_reflection.json`, `personal_pending.json` (rw) | exit 0; watermarks advance; MEMORY.md ≤8192B after eviction |
| `scripts/memory_dream.py` | Extract processes/decisions → playbook/; adaptive gate; decision-rationale loop | `[--dry-run] [--since-days N] [--deliver-questions] [--reconcile]` | `dream` | `dream.json`, `decision_questions.json` (rw) | exit 0; `dream.json` watermark advances; playbook entries written |
| `scripts/memory_flush.py` | Transcript consolidator (hooks); work/personal split; 2KB floor; incremental watermark | `<transcript-path>` | `memory_flush` | `last_flush.json`, `flush_offsets.json` (rw) | exit 0; ≥2KB transcript → bullets in daily log or inbox capture |
| `scripts/company_brain_reflect.py` | Profile-agnostic company reflect+dream (LinOS); leadership/gap digests + playbooks | `reflect\|dream --profile P [--since-days N] [--dry-run]` | `company-brain-reflect` | `company_brain_reflect_<profile>.json` (rw) | exit 0; digest docs created; watermark advances |

## 3 · Heartbeat / Proactive (individual role)

| Path | Purpose | CLI / flags | `CLAUDE_INVOKED_BY` | State | Health signal |
|---|---|---|---|---|---|
| `scripts/heartbeat.py` | 5-stage proactive loop: reindex → gather → diff → hygiene → guardrail→agent→notify | `[--dry-run] [--no-agent] [--force]` | `heartbeat` | `heartbeat-state.json`, `heartbeat-monitor-state.json` (rw) | exit 0; snapshot saved; daily-log tick appended |
| `scripts/heartbeat_snapshot.py` | Deterministic snapshot build + diff | module | — | `heartbeat-state.json` (rw) | snapshot reproducible; diff = added/removed sets |
| `scripts/drafts.py` | Draft lifecycle: filename hash, 24h expiry, sent voice corpus | module | — (writes `drafts/`) | same `(source,id)` → same filename; frontmatter parses |
| `scripts/habits.py` | HABITS.md daily reset + per-pillar signal detection (zero-LLM) | module | — (writes `HABITS.md`) | reset creates fresh Today; signals boolean |
| `scripts/gap_analysis.py` | Deterministic mtime stale-entity scan (projects/clients/goals) | `[--json] [--days N] [--folders …]` | — | `gap-analysis-state.json` (rw) | exit 0; gaps listed; last-surfaced tracked |

## 4 · Integrations

| Path | Purpose | CLI | State | Health signal |
|---|---|---|---|---|
| `scripts/query.py` | Single dispatcher; lazy-imports per registry | `<integration> <subcmd> [args]` | — | exit 0 on integration success |
| `scripts/integrations/registry.py` | Central registry (name, env_var, module) | module | — | INTEGRATIONS has 6+ entries; `enabled(spec)` reflects env |
| `…/slack.py` | Read (channels/since/mentions/dms/thread) + send/reply | `query.py slack …` | `slack-state.json` (rw: cursors + bot_user_id) | state has channels dict + bot_user_id; `recent()` returns list |
| `…/github.py` | Issues/PRs/commits read; create-issue; draft-PR | `query.py github …` | — | exit 0; list returned; rate-limit floor 50 |
| `…/clickup.py` | Multi-workspace overdue/today; create; status | `query.py clickup …` | — | exit 0; tasks w/ ms-epoch dates; workspaces parsed |
| `…/gmail.py` | Unread/recent metadata + fetch_since body (feeder) | `query.py gmail …` | — (token via _google) | exit 0; headers or full messages; body base64-decoded |
| `…/calendar.py` | Today/week events (BRT, read-only) | `query.py calendar …` | — | exit 0; events w/ start/end ISO |
| `…/rss.py` | Polite etag/modified polling; per-feed try/except; 200-id FIFO | `query.py rss …` | `rss-state.json` (rw) | exit 0; items returned; seen_ids capped |
| `…/_google.py` | Shared Google OAuth (token load + auto-refresh) | module | `google_token.json` (rw on refresh) | `_creds()` valid; token has refresh_token |

## 5 · Comms-capture feeders

| Path | Purpose | CLI | `CLAUDE_INVOKED_BY` | State | Health signal |
|---|---|---|---|---|---|
| `scripts/comms_capture.py` | Per-channel high-signal distiller (Slack, Gmail); registry-gated; own cursor; scrub | `[--dry-run] [--since-hours N]` | `comms-capture` | `comms-capture-cursors.json` (rw), `comms-capture-state.json` (reporter) | exit 0; cursor advances over scanned msgs; ≥min_messages → capture |

## 6 · Federation

| Path | Purpose | CLI | `CLAUDE_INVOKED_BY` | State | Health signal |
|---|---|---|---|---|---|
| `scripts/linos_consumer.py` | LinOS consumer: read cleared captures → joint/ + LINMEMORY + ack | `[--dry-run] [--slug S]` | `linos-consumer` | `consumer_watermark.json` (rw) | exit 0; watermark advances; joint docs + ack manifests created |
| `scripts/federation_doctor.py` | Per-inbox observability (captured/cleared/rsynced/acked/staleness) + canary | `[--inbox S] [--canary] [--json] [--alert]` | — | `federation-doctor-state.json` (rw) | exit 0; table/JSON; `--alert` pings healthcheck |
| `deploy/bin/sync_cleared_inbox.py` | VPS→LinOS push; DUAL gate (scope + cleared); rsync `--update` no `--delete` | `[--dry-run]` | — | `linos-inbox-sync-state.json` (reporter) | exit 0; only cleared+in-scope mirrored; counts logged |
| `deploy/bin/sync_acks.py` | LinOS→bruno ack return (`--ignore-existing`) | `[--dry-run]` | — | `linos-ack-sync-state.json` | exit 0; acks mirrored to bruno drop |
| `deploy/bin/retire_vps_inbox.py` | VPS retire: processed AND acked ELSE 15-day fallback; never quarantined | `[--apply] [--min-age-hours N]` | — | `retired_inbox.json`, `inbox-retired-excludes.txt` | dry-run default; retires only terminal+acked; ledger written |
| `deploy/bin/retire_local_inbox.py` | Mac self-prune once VPS holds terminal | `[--apply] [--min-age-hours N]` | — | (reporter state) | dry-run default; deletes only if VPS terminal set non-empty |

## 7 · Monitoring (Track D)

| Path | Purpose | CLI | State | Health signal |
|---|---|---|---|---|
| `scripts/sync_common.py` | Shared reliability runtime: status file + rate-limited Slack alert + healthcheck ping + run-lock | module (`make_reporter`, `report_outcome`) | `<svc>-state.json`, `locks/<svc>.run.lock` | `SyncReporter` instantiates; status JSON written; healthcheck pinged if URL set |
| `scripts/memory_doctor.py` | sqlite quick_check + index freshness (3h) + search canary | `[--dry-run] [--skip-canary] [--staleness-hours N]` | `memory-doctor-state.json` | exit 0; DB healthy; staleness ≤ thresh; canary ≥1 result |
| `scripts/slackbot_watchdog.py` | Unit-down / restart-storm / duplicate-instance / auth.test | `[--dry-run] [--skip-smoke] [--unit N]` | `slackbot-watchdog-state.json` | exit 0; unit active + 1 process + auth OK |
| `scripts/provision_healthchecks.py` | Idempotent upsert of healthchecks.io checks per brain×host; emits env block | `--brain B --host H [--services …] [--dry-run] [--json]` | — (HTTP) | exit 0; checks created/updated; env vars printed |
| `scripts/vault_sync.py` | Reliable vault git-sync; self-heals config; concat-both; never broken tree | `[--dry-run] [--emit-alert MSG]` | `vault-sync-state.json`, run-lock | exit 0; merge succeeds or aborts clean; healthcheck pinged |
| `scripts/code_sync.py` | VPS pull-only ff-merge; stash dirty; recycle slackbot on code change | `[--dry-run]` | `code-sync-state.json`, run-lock | exit 0; ff-only pull; slackbot recycled on in-process code change |

## 8 · Security

| Path | Purpose | CLI | Health signal |
|---|---|---|---|
| `scripts/sanitize.py` | Trust boundary: `wrap_external`, `scrub_secrets`, `scrub_excluded_entities`, injection-marker strip | module | `wrap_external` balanced tags; secret patterns match keys/JWTs/CPF/CNPJ |
| `hooks/block-secrets.py` | PreToolUse: block credential paths + env-exfil bash; `*.example` carve-out | hook | soft-block `{decision:block}` on `.env` read; pass otherwise |
| `hooks/dangerous-bash.py` | PreToolUse (Bash): hard-block destructive/privilege/exfil patterns | hook | exit 2 on `rm -rf /`; exit 0 otherwise |
| `hooks/protect-soul.py` | PreToolUse (Edit/Write): block SOUL.md under reflection | hook | block SOUL.md write under `CLAUDE_INVOKED_BY=reflection`; pass otherwise |
| `hooks/session-start-context.py` | SessionStart: inject vault context | hook | injects context block; `build_context()` importable |
| `tests/test_privacy_gate.py` | Canary leak-test: plant secrets/PII/excluded names → assert absent from cleared output | `uv run python tests/test_privacy_gate.py` | **exit 0 = zero leaks** (CI gate before any pilot) |

## 9 · Chat bot

| Path | Purpose | CLI | `CLAUDE_INVOKED_BY` | State | Health signal |
|---|---|---|---|---|---|
| `chat/bot.py` | Socket Mode daemon; DM + @mention → stateful SDK session per thread | `[--smoke-test]` | `chat` | `slack-state.json` (bot_user_id), `chat.db` | `--smoke-test` exit 0 (auth.test); daemon stays connected |
| `chat/system_prompt.py` | Per-session prompt: identity + vault context + mrkdwn rules | `build_chat_system_prompt()` | — | — | prompt includes SOUL/USER/MEMORY |
| `chat/session_manager.py` | Per-thread client cache; idle reap 60min; LRU cap 4; flush-on-close | class | — | `chat.db` (rw) | `get_or_create` returns client; reaper loops; flush on close |
| `chat/channel_registry.py` | Company-brain channel access gate (registry + allowlist) | `resolve_slack_event(...)` | — | `brain-config.json` (r) | decision.allowed for registered+enabled+allowlisted |
| `chat/adapters/slack_adapter.py` | Bolt event registration (DM + @mention routing) | `register(...)` | — | — | events routed; replies threaded |

**J8 runtime tool-config signal** (role-aware): the chat options factory at `chat/bot.py` (`allowed_tools=["Read","Write","Edit","Bash"]` + `setting_sources=["project"]`, ~L83-84) is the deterministic source — grep it, and confirm the running daemon's startup log / session journal reflects both. Then verify the **expected skills** exist in the brain's skills dir: universal `vault-structure`(*) + `memory-search`; individual `+ dev-task`; company `+ company-{judge,query,leadership-digest,gap-analyst,consolidator,standards-review}`. (*) `vault-structure`/`memory-search` are **bootstrap-generated, brain-local** (each describes its own vault) — BrunOS's instance is currently the `brunos-vault` skill (legacy name).

## 10 · Deploy / config helpers

| Path | Purpose | CLI | State | Health signal |
|---|---|---|---|---|
| `scripts/brain_config.py` | Per-brain config store (DEFAULTS deep-merged w/ state file) | `get(dotted.path)` | `brain-config.json` (r, optional) | `get()` returns merged dict; absent file → pure defaults |
| `scripts/gen_schedules.py` | Generate timer units from brain-config cadence strings | `[--platform mac\|vps\|both] [--dry-run]` | — (writes deploy/) | exit 0; byte-identical on re-run (idempotent) |
| `scripts/bootstrap_google_oauth.py` | One-time OAuth consent (Gmail+Calendar) | (interactive) | `google_token.json` (w) | exit 0; token w/ refresh_token |
| `scripts/notify_adapter.py` | Pluggable "ask the person" surface (Slack default / none) | `get_adapter(name)` | — | `SlackAdapter.ask()` True on send; NoneAdapter False |
| `deploy/bin/sync_inbox.py` | Mac→VPS rsync of gitignored `_inbox/` (`--update`, no `--delete`) | (launchd) | `inbox-rsync-state.json` | exit 0; captures pushed; respects exclude file |
| `scripts/shared.py` | Cross-cutting utils: BRT time, file lock, state I/O, vault path, slug, capture parse, `validate_consumer_read`, `write_inbox_capture`, `dispatch_flush` | module | (writes callers' state) | `load_state` returns dict/default; `now_brt()` TZ-aware; `vault_path()` resolves |

---

## `CLAUDE_INVOKED_BY` registry (recursion guard — check A7)

`chat` · `reflection` · `dream` · `memory_flush` · `heartbeat` · `codex-watcher` ·
`codex-backfill` · `linos-consumer` · `company-brain-reflect` · `comms-capture` ·
`vault-sync` · `code-sync`. Every long-running / SDK-spawning script sets its value
**before** importing `claude_agent_sdk`.
