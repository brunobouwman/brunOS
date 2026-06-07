# Brain Diagnosis — BrunOS (2026-06-07)

**Brain:** BrunOS · **Role:** individual · **Host:** Mac (failover) — diagnosis run from a git worktree
**Vault root:** `/Users/brunobouwman/Documents/brunOS-brain/BrunOS` · **Code repo:** worktree `thirsty-matsumoto-1cb353`
**Federation:** on (producer) · **Trigger:** full sweep (skill smoke-test)
**Boundary attestation:** diagnosed only BrunOS, read-only. Vault `git status` shows only a pre-existing edit (`brunos_followups.md`) — the diagnosis wrote nothing to the vault; the privacy canary uses a synthetic temp corpus.

> **Host caveat (shapes every runtime finding).** This ran on the **Mac failover host from a worktree**. Two consequences: (1) the worktree's `BrunOS/` is gitignored/empty — all vault checks must use `shared.vault_path()` (which resolves to the real main-repo vault), never a relative `BrunOS/` path; (2) the **primary runtime is the VPS** — reflection, dreaming, heartbeat, cleared-push and all timers run there, so their state/freshness **cannot be authoritatively verified from Mac**. Those are marked *could-not-verify (VPS-side)* rather than failures. **For an authoritative health read, run this skill on the VPS primary.**

## Scorecard

```
A Foundation        ✅ 7/7
B Memory/RAG         ⚠️ 5/6   B2 index 6.1h stale — EXPECTED on Mac (reindex runs VPS-side)
C Consolidation      ✅ 2/2 verifiable (C4 31 playbook, C5 under cap); C1/C2/C3 VPS-side n/v
D Proactive          ✅ 2/2 verifiable (D3 drafts, D4 habits); D1/D2/D5 VPS-side n/v
E Integrations       ✅ 2/2 verifiable (E1 6 regd, E4 slack-state); E2/E3/E5 not deep-run
F Chat               ✅ 2/2 verifiable (F1 smoke OK, F3 chat.db); F2 cross-host n/v
G Federation(prod)   ✅ gates 4/4 (G2/G3/G4/G6); G1 3.1d (VPS-side clear) · G5 VPS-side n/v
H Federation(cons)   n/a (individual role)
I Security           ✅ 6/6   ← all privacy gates intact
J Deploy/Monitoring  ✅ 2/2 verifiable (J1 12 plists, J7 merge driver); J2–J6 VPS/cross-host n/v
─────────────────────────────────────────────
CRITICAL: 0 · DEGRADED: 1 (B2, expected) · INFO: 1 (G1, VPS-side)
RELEASE-BLOCKERS: none — critical safety floor fully GREEN
```

**Verdict: BrunOS is healthy.** Every release-blocker check (G2/G3/H2-n/a/I1/I5/J6) that is verifiable from this host passes; nothing actionable beyond confirming two VPS-side signals. No code or vault defects found.

## Critical safety floor (always-run release-blocker checks)

| Check | Status | Evidence |
|---|---|---|
| I5/G3 privacy-gate canary | ✅ | `tests/test_privacy_gate.py` exit 0 — **72 passed, 0 failed, 0 skipped** |
| G2 dual gate | ✅ | `sync_cleared_inbox.py`: imports `validate_consumer_read` + `CONSUMER_READ_SCOPES` (L59); `_is_eligible(fm, consumer)` (L88) enforces scope AND `share_status=="cleared"` |
| G4 excluded-entities | ✅ | `Memory/_excluded-people.md` present; loaded by `sanitize.py:106` (`scrub_excluded_entities`) |
| I1 hook order | ✅ | `settings.json` PreToolUse order = `['block-secrets','dangerous-bash','protect-soul']` |
| I6 sanitize | ✅ | `wrap_external` / `scrub_secrets` / `scrub_excluded_entities` all callable |
| J6 single-instance | n/v | cross-host — confirm via VPS `slackbot_watchdog.py` (Mac bot is `Disabled`) |
| F2 one bot process | n/v | cross-host — same watchdog confirms |

## Findings (non-pass + notable)

| Check | Status | Evidence | Symptom or cause? |
|---|---|---|---|
| B2 | ⚠️ degraded (expected) | newest vault md `18:09` vs `memory.db` `12:04` → 6.1h | EXPECTED on Mac: reindex runs in the VPS heartbeat (disabled on failover). Not a defect. |
| G1 | ℹ️ info (VPS-side) | 13 inbox captures, all 13 uncleared, 0 quarantined, oldest **3.1d** | Mac holds **producer originals**; clearing is a VPS-side mutation that doesn't sync back. The Mac `inbox-retire` job is intentionally `Disabled` (CLAUDE.md deferred). Confirm VPS cleared them via `federation_doctor.py` on the VPS. |
| A6 | ✅ (info) | `brain-config.json` absent → pure `brain_config.DEFAULTS` | Correct — absent file = defaults, role resolves to `individual`. |

**Healthy highlights:** A4 frontmatter **0/191 files** missing required keys · memory.db `quick_check ok`, 4/4 tables, **70 edges / 1017 chunks** · MEMORY.md **5916B** (under 8KB cap) · B4 search canary 3 hits · 31 playbook entries · F1 bot auth.test OK (`bot_user_id=U0B1A4QKB4J`, team Brains) · J7 concat-both merge driver registered.

## Root-cause analysis

No root-cause chains — there are no genuine failures. The two non-✅ items (B2, G1) both reduce to the same benign cause: **this is the Mac failover host, where the mutating services (reindex, reflection-clear) run VPS-side by design.** Both resolve by reading from the VPS, not by any fix.

---

# Remediation Plan — BrunOS

_No release-blockers and no defects. Two optional VPS-side confirmations + two skill-precision improvements surfaced by this smoke-test run._

### Task 1 — Confirm VPS-side health authoritatively (closes the n/v gaps) · severity: info
- **Goal:** re-run this skill (or the targeted doctors) **on the VPS primary** to verify the could-not-verify checks: C1/C2/C3 reflection+dream watermarks, D1/D2 heartbeat freshness, G1/G5 cleared-push + capture clearing, J2–J6 timers + single-instance.
- **Files:** none — operational.
- **Validation:** `ssh` to VPS, run `memory_doctor.py`, `federation_doctor.py`, `slackbot_watchdog.py` (all `--dry-run`/read-only) and confirm green.
- **Acceptance:** VPS reports fresh index (<3h), captures clearing (oldest uncleared ≤3d), one bot process.

### Task 2 — Confirm Mac producer captures are retiring (closes G1 follow-through) · severity: info
- **Goal:** verify the 13 uncleared Mac-local captures are terminal+acked on the VPS so the (currently disabled) Mac `inbox-retire` can prune them — matches the CLAUDE.md deferred "enable Mac inbox-retire after dry-run review".
- **Validation:** `retire_local_inbox.py` (dry-run default) on Mac → confirms VPS holds the terminal set; review output before enabling the launchd unit.
- **Acceptance:** dry-run shows the captures are VPS-terminal; decide on enabling `com.bruno.brunos.inbox-retire`.

### Task 3 — Tighten the skill's A7 recursion-guard check (skill precision) · severity: info
- **Root cause:** the A7 check greps every module for `CLAUDE_INVOKED_BY` and false-flagged `chat/session_manager.py`, which imports the SDK **lazily inside functions** and is never an entry point — the guard is correctly set by `bot.py` (L19) before the first SDK import (L79).
- **Files:** `.claude/skills/diagnose-brain/references/health-signals.md` / `diagnosis-checklist.md` A7 note.
- **Fix:** A7 should verify the **entry-point** sets the guard before its first SDK import, and treat lazy in-function SDK imports in helper modules as covered by the entry-point guard.
- **Acceptance:** A7 passes BrunOS without flagging `session_manager.py`.

### Task 4 — Document the host-scope caveat in the skill (skill precision) · severity: info
- **Root cause:** running from a worktree on the failover Mac makes many runtime checks non-authoritative; the skill handled it but should make it explicit up front.
- **Fix:** add a Phase-0 note: detect failover-vs-primary + worktree, and either (a) prefer running on the primary host, or (b) clearly tag VPS-side checks `n/v` instead of `❌`. Always resolve vault paths via `shared.vault_path()`, never relative `BrunOS/`.
- **Acceptance:** the skill's Phase 0 instructs host/worktree detection and path resolution.

## Handoff

- **Suggested route:** Tasks 1–2 are operational (run on VPS, no code). Tasks 3–4 are small skill-doc edits — fold into the skill now since we're iterating on it.
- **Do NOT auto-apply** — Bruno triggers remediation per-task.
- **Could-not-verify (VPS-side, by design from Mac):** C1/C2/C3, D1/D2/D5, E2/E3/E5, F2, G1(final)/G5, I2/I3/I4 (hook-fire), J2/J3/J4/J5/J6.
