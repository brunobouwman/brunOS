# Diagnosis Checklist — what a healthy brain looks like

> The runnable spec for the diagnosis skill. For the target brain, walk these checks:
> **PRESENCE** (component installed/importable) → **CONFIG** (configured for its role) →
> **HEALTH** (running + producing fresh output) → **INTEGRITY** (privacy/federation gates
> hold). Report each ✅ pass / ⚠️ degraded / ❌ missing-or-broken, with the remediation hint.
>
> **Synced from** the BrunOS vault: `Memory/projects/Brain/architecture/05-diagnosis-checklist.md`.
> The vault copy is the living source of truth; trust observed code over this doc if they drift.

The **Role** column gates applicability: `all` · `ind` (individual only) · `co` (company
only) · `prod` (producer = individual with `reflection.federation == true`). Skip checks
that don't apply to the Phase-0 role.

## How to run

1. **Determine role** — `brain_config.get("role")` (`individual`|`company`; absent → `individual`)
   and `brain_config.get("reflection.federation")`. Role gates which checks apply.
2. **Run the checks** below per subsystem, preferring non-destructive signals (file mtimes,
   `--dry-run`, `--smoke-test`, sqlite `quick_check`, importability) from `health-signals.md`.
3. **Classify** each: present? configured? healthy? Emit a report + remediation list.
   Privacy/federation integrity failures are **critical**; missing proactive features are
   **degraded**; missing optional feeders are **info**.
4. **Always run the critical safety floor** (G2/G3/H2/I1/I5/J6/F2) regardless of hint.

---

## A · Foundation (every brain)

| # | Role | Check | How | Severity if fail |
|---|---|---|---|---|
| A1 | all | Code repo present + `uv sync` clean | `.claude/scripts/` exists; `uv run python -c "import claude_agent_sdk"` | critical |
| A2 | all | Vault path resolves | `shared.vault_path()` returns existing dir; `BRUNOS_VAULT_PATH` set | critical |
| A3 | all | `.claude/.env` present + key tokens | env file exists (not `.env.example`); required tokens for enabled integrations | critical |
| A4 | all | Vault frontmatter uniform | every `Memory/**.md` has `type/created/updated/tags/status` | degraded |
| A5 | all | Singletons present | `SOUL.md`, `USER.md`/`COMPANY.md`, `MEMORY.md`/`LINMEMORY.md`, `HEARTBEAT.md`, `HABITS.md` | critical (identity) |
| A6 | all | `brain-config.json` valid | `brain_config.get()` returns merged dict; role set | degraded (absent = defaults, OK) |
| A7 | all | Recursion guards intact | every **entry-point** sets `CLAUDE_INVOKED_BY` before its first `claude_agent_sdk` import. NB: helper modules (e.g. `chat/session_manager.py`) that import the SDK **lazily inside functions** are covered by the entry point (`bot.py`) that set the guard first — a module-level grep false-flags them; verify the entry point, not every module | critical (loop risk) |

## B · Memory / RAG

| # | Role | Check | How | Severity |
|---|---|---|---|---|
| B1 | all | `memory.db` healthy | `PRAGMA quick_check` passes; `chunks`+`chunk_vec`+`chunk_fts`+`edges` tables exist | critical |
| B2 | all | Index fresh | newest vault `.md` mtime − `memory.db` mtime < 3h (or run `memory_doctor.py`) | degraded |
| B3 | all | Edges populated | `edges` table non-empty (requires a `--full` pass post-deploy) | info (graph no-op if empty) |
| B4 | all | Search canary | `memory_search.py "<known query>"` returns ≥1 result | critical |
| B5 | all | Embedding cache | `fastembed_cache/` present; `embeddings.embed_query` callable | degraded |
| B6 | all | MEMORY.md within cap | ≤ 8192 bytes; over-cap items in `_archive/MEMORY-archive.md` | degraded |

## C · Consolidation (reflection + dreaming)

| # | Role | Check | How | Severity |
|---|---|---|---|---|
| C1 | all | Reflection ran recently | `last_reflection.json` + `inbox_reflection.json` watermarks advanced within cadence | degraded |
| C2 | all | Personal buffer drains | `personal_pending.json` not growing unbounded (curation runs daily) | degraded |
| C3 | all | Dreaming ran / gated correctly | `dream.json` watermark advanced, OR < `trigger_min_captures` new (legit skip) | info |
| C4 | all | Playbook populated | `Memory/playbook/` has entries (for a brain with history) | info |
| C5 | all | Curation cap-guard | MEMORY.md ≤8KB after last curation; no `curate_memory_over_cap` alert | degraded |
| C6 | co | Company-brain consolidation | `company_brain_reflect.py` produced recent `digests/leadership/` + `digests/gaps/` | degraded |

## D · Proactive (individual role only — company brains have no heartbeat)

| # | Role | Check | How | Severity |
|---|---|---|---|---|
| D1 | ind | Heartbeat ticking | `heartbeat-state.json` mtime within last tick window (08–22 BRT) | degraded |
| D2 | ind | Heartbeat not erroring | `heartbeat-monitor-state.json` `consecutive_failures` == 0; no guardrail-block alert storm | degraded |
| D3 | ind | Drafts lifecycle | `drafts/active\|sent\|expired/` exist; no >24h drafts stuck in active | info |
| D4 | ind | Habits resetting | `HABITS.md` has today's "Today" section | info |
| D5 | ind | Gap analysis wired | `gap-analysis-state.json` present; heartbeat stage 4b runs | info |

## E · Integrations

| # | Role | Check | How | Severity |
|---|---|---|---|---|
| E1 | all | Registry complete | `integrations/registry.py` lists the 6; `enabled()` matches env | degraded |
| E2 | all | Each enabled integration authenticates | `query.py <int> <read-subcmd>` exits 0 (or `--smoke`) | degraded per-int |
| E3 | all | Google OAuth valid | `google_token.json` has `refresh_token`; `_google._creds()` valid | degraded (gmail/cal) |
| E4 | all | Slack state sane | `slack-state.json` has `bot_user_id` + channels map | degraded |
| E5 | all | RSS polling | `rss-state.json` per-feed cursors advancing | info |

## F · Chat bot

| # | Role | Check | How | Severity |
|---|---|---|---|---|
| F1 | all | Bot connects | `bot.py --smoke-test` exits 0 (auth.test) | degraded |
| F2 | all | **Single instance** | exactly one `bot.py` process across all hosts (watchdog confirms) | **critical (dupes)** |
| F3 | all | Thread index | `chat.db` present with `chat_threads` table | info |
| F4 | co | Profile correct | `CHAT_BRAIN_PROFILE`, registry enforcement, allowlist set | critical (company) |

## G · Federation — producer (individual brain that shares)

Apply only when `role == individual` AND `reflection.federation == true`. Skip entirely
for a solo brain (`federation:false`) — absent units are a ✅, not a ❌.

| # | Role | Check | How | Severity |
|---|---|---|---|---|
| G1 | prod | Captures clearing | oldest uncleared capture ≤ 3 days; `federation_doctor.py` green | critical |
| G2 | prod | **Dual-gate intact** | `validate_consumer_read` + `share_status==cleared` both enforced in `sync_cleared_inbox.py` | **critical (release-blocker)** |
| G3 | prod | **Scrub fail-closed** | `tests/test_privacy_gate.py` exits 0 (canary zero-leak) | **critical (release-blocker)** |
| G4 | prod | Excluded-entities present | `_excluded-people.md` exists + loaded by clear pipeline | critical |
| G5 | prod | Cleared push running | `linos-inbox-sync-state.json` recent; eligible/out-of-scope/uncleared counts sane | degraded |
| G6 | prod | Quarantine not growing | few/no `share_status:quarantined` captures (stuck-clear signal) | degraded |

## H · Federation — consumer (company brain)

Apply when `role == company`.

| # | Role | Check | How | Severity |
|---|---|---|---|---|
| H1 | co | Consumer running | `consumer_watermark.json` advanced; `linosbrain-consumer` timer active | critical |
| H2 | co | **Read gate intact** | consumer re-checks dual gate read-only before integrating | **critical (release-blocker)** |
| H3 | co | Joint docs produced | `Memory/joint/<slug>/` populated; `LINMEMORY.md` appended | degraded |
| H4 | co | Acks written + returned | `_acks/brunos/*.json` written; `sync_acks.py` mirroring to producer drop | critical (F2 loop) |
| H5 | co | Identity seeded | company SOUL/COMPANY/STANDARDS/DECISIONS/ACCESS_POLICY present | critical |

## I · Security (every brain)

| # | Role | Check | How | Severity |
|---|---|---|---|---|
| I1 | all | **Hooks registered + ordered** | `settings.json`: block-secrets → dangerous-bash → protect-soul | **critical (release-blocker)** |
| I2 | all | block-secrets fires | attempt `.env` read → blocked | critical |
| I3 | all | dangerous-bash fires | `rm -rf /` pattern → exit 2 | critical |
| I4 | all | protect-soul fires | SOUL.md edit under `CLAUDE_INVOKED_BY=reflection` → blocked | degraded |
| I5 | all | **Canary CI gate green** | `tests/test_privacy_gate.py` exit 0 | **critical (release-blocker)** |
| I6 | all | sanitize importable | `wrap_external`/`scrub_secrets`/`scrub_excluded_entities` callable | critical |

## J · Deployment / monitoring

| # | Role | Check | How | Severity |
|---|---|---|---|---|
| J1 | all | Expected units installed | per role, the `*osbrain-*` set from the deployment topology present | degraded |
| J2 | all | Units enabled (per role + host) | timers enabled on primary; failover disabled | degraded |
| J3 | all | Cadence pinned to BRT | generated `OnCalendar` has `America/Sao_Paulo` | degraded (3h drift) |
| J4 | all | Healthchecks provisioned | `<BRAIN>_<SVC>_HEALTHCHECK_URL` set for instrumented services; checks exist | degraded |
| J5 | all | Sync services green | `vault-sync-state.json` + `code-sync-state.json` `consecutive_failures`==0 | degraded |
| J6 | all | **Single-instance honored** | no dual-run of slackbot/heartbeat/reflect/dream across hosts | **critical (release-blocker)** |
| J7 | all | Merge driver registered | `concat-both` in vault `.git/config` (per-clone) | degraded (daily-log conflicts) |

---

## Report shape

```
BRAIN: <name>  ROLE: <individual|company>  HOST: <label>
─────────────────────────────────────────────
A Foundation        ✅ 7/7
B Memory/RAG         ⚠️ 5/6   B2 index stale (4.2h) → run memory_index.py
C Consolidation      ✅ 6/6
D Proactive          ✅ 5/5
E Integrations       ⚠️ 4/5   E3 google_token expired → re-run bootstrap_google_oauth
F Chat               ✅ 4/4
G Federation(prod)   ✅ 6/6
I Security           ✅ 6/6   ← privacy gates intact
J Deploy/Monitoring  ❌ 5/7   J4 healthchecks unprovisioned → provision_healthchecks.py
─────────────────────────────────────────────
CRITICAL: 0 · DEGRADED: 3 · INFO: 1
REMEDIATION → [3 dev-task cards drafted]
```

**Severity policy:** any ❌ in G2/G3/H2/I1/I5/J6/F2 (privacy, federation gates,
single-instance) is a **release-blocker** — a brain failing these must not federate or
go to a client. Everything else is fix-forward.
