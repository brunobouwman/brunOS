---
name: diagnose-brain
description: >-
  Whole-system health diagnosis for a second brain (BrunOS, LisaOS, LinOS, or a
  client brain). Walks the role-aware diagnosis checklist (foundation, memory/RAG,
  consolidation, proactive, integrations, chat, federation, security, deployment),
  root-causes what's broken via the data flows, and emits a scored report + an
  ordered, atomic remediation plan. Use whenever a monitoring alert fires, a
  healthcheck goes red, a service is failing, or you need to know whether a brain is
  healthy — "diagnose the brain", "the brain is broken", "memory-doctor is failing",
  "what's wrong with LisaOS", "brain doctor", "the federation alert went off", "is
  this brain healthy", "run a diagnosis". Accepts an OPTIONAL hint (an alert payload,
  a healthcheck name, a subsystem, or a symptom) to focus the deep-dive; with no hint
  it runs a full sweep. Role-aware: works for individual AND company brains. Runs
  read-only and only against the brain it is invoked inside.
---

# Diagnose Brain

Diagnose the health of the brain you are running inside, root-cause what's broken,
and produce a scored report + an ordered remediation plan. This is the **detect +
diagnose** tool the monitoring stack hands off to when something goes wrong — and the
on-demand "is this brain healthy?" check before a pilot or a federation go-live.

It is the broad, whole-system evolution of `federation-parity-audit` (which audits
only the federation producer slice). For a deep federation **producer-contract**
audit specifically, defer to that skill; this one covers all subsystems A–J and
reuses its federation contract for the federation deep-dive.

## Two hard rules

1. **Self-diagnosis only.** Diagnose ONLY the brain you are invoked inside. Never
   open, read, clone, or infer another brain's vault or repo (BrunOS must not read
   LisaOS; the `0700`-home isolation in deployment is deliberate). All evidence comes
   from THIS brain's local vault + code repo.
2. **Non-destructive.** Every check is read-only. Prefer file mtimes, `--dry-run`,
   `--smoke-test`, sqlite `PRAGMA quick_check`, importability, and `grep`. Never run a
   real reflection/clear/index/flush that mutates state. After any `--dry-run`, confirm
   `git status Memory` shows no writes.

## Inputs

- **`hint`** (optional) — focuses the deep-dive. Any of:
  - an **alert payload** ("brunos-memory-doctor-vps is DOWN", a Slack alert body),
  - a **healthcheck / service name** (`memory-doctor`, `linos-inbox-sync`),
  - a **subsystem** (a checklist letter `B`, or "memory", "federation", "chat"),
  - a **symptom** ("chat bot posting duplicate replies", "captures not clearing").
- **No hint** → full sweep across every subsystem applicable to the brain's role.

The hint only reorders and deepens Phase 3. The **critical safety floor (Phase 2)
always runs**, hinted or not — an alert about one thing frequently rides on a deeper
break, and the release-blocker checks must never be skipped.

## References (read these before diagnosing)

- [references/diagnosis-checklist.md](references/diagnosis-checklist.md) — the runnable
  A–J checklist with per-check severity and role applicability. The spine of the run.
- [references/health-signals.md](references/health-signals.md) — per-component path ·
  CLI · state file · the exact health-signal assertion to run.
- [references/data-flows.md](references/data-flows.md) — the end-to-end sequences, for
  tracing a failing check back to its upstream cause (symptom → root cause).
- `../federation-parity-audit/references/federation-contract.md` — the federation
  producer contract (C1–C10); use for the **G** (producer) deep-dive instead of
  re-deriving the checks.

> These references are **ported from the canonical BrunOS vault architecture folder**
> (`Memory/projects/Brain/architecture/`, docs 02/03/05). That vault folder is the
> living source of truth — when the code changes, it is updated there first, then
> re-synced here. If a check here disagrees with observed code, trust the code and
> note the drift in the report.

## Workflow

Run the phases in order.

### Phase 0 — Orient

- Confirm which brain this is. Locate its two roots: the **vault** (`Memory/` tree)
  and the **code repo** (`.claude/scripts/` + `.claude/hooks/`). They may be one repo
  or two. If ambiguous, ask — do not guess.
- **Always resolve the vault via `shared.vault_path()`, never a relative `BrunOS/` path.**
  The vault is often gitignored and may be empty in a worktree/clone — a relative path
  silently checks the wrong (empty) tree and false-flags every vault check as missing.
- **Determine role.** Read `brain-config.json` via `brain_config.get("role")`
  (`individual` | `company`; absent file → defaults → `individual`) and
  `brain_config.get("reflection.federation")`.
- **Determine host scope — primary vs. failover.** Identify the host (Mac / VPS
  namespace) AND whether the mutating services run here. On a **failover host** (e.g.
  Mac with timers `Disabled`) or in a **worktree**, the index, reflection/dream
  watermarks, heartbeat state, cleared-push, and timers all run *elsewhere* (the VPS
  primary). Mark those checks **`n/v` (could-not-verify, VPS-side)** — NOT `❌`. For an
  authoritative read, prefer running on the primary host. Stale index / uncleared
  local captures / absent watermarks on a failover host are **expected**, not failures.
- State the boundary out loud: "I will diagnose only <this brain>, read-only."
- **Role gates the checklist** (see the table below). Don't flag a company brain for a
  missing heartbeat, or an individual brain for a missing consumer.

### Phase 1 — Triage

- **With a hint:** map it to the subsystem(s) it touches and the data flow it lives in
  (use data-flows.md). E.g. `memory-doctor down` → subsystem **B** + Flow "heartbeat
  reindex" / the index-freshness signal; "duplicate replies" → **F2** single-instance +
  Flow 4 (chat). Note the 1–2 most-likely subsystems to deep-dive first.
- **Without a hint:** mark a full sweep of all role-applicable subsystems A→J.
- Either way, write down the plan before running checks.

### Phase 2 — Critical safety floor (ALWAYS, regardless of hint or focus)

Run these release-blocker checks every single time. A ❌ in any is a **release-blocker**
— the brain must not federate or go to a client until it's fixed:

- **G2 / G3** (producer brains) — dual gate (`validate_consumer_read` + `share_status==cleared`)
  enforced in `sync_cleared_inbox.py`; `tests/test_privacy_gate.py` exits 0 (zero-leak canary).
- **H2** (company brains) — consumer re-checks the dual gate read-only before integrating.
- **I1 / I5** — hooks registered + ordered (block-secrets → dangerous-bash → protect-soul);
  privacy-gate canary green.
- **J6** — single-instance honored: no dual-run of slackbot / heartbeat / reflect / dream
  across hosts.
- **F2** (if chat enabled) — exactly one `bot.py` process across all hosts.

If any safety-floor check fails, it leads the report and the remediation plan,
ahead of whatever the hint pointed at.

### Phase 3 — Deep dive

- **Hinted:** start with the triaged subsystem(s), then walk their **upstream and
  downstream neighbors** in the data flow (a broken index is often an upstream sync or
  a downstream search-canary symptom). Expand outward until the failure is bounded.
- **Full sweep:** walk A→J in order, role-gated.
- For each check, run the non-destructive health signal from health-signals.md /
  diagnosis-checklist.md. Classify: ✅ pass · ⚠️ degraded · ❌ missing-or-broken.
- Record file path + line (or the command + its output) as evidence for every finding.

### Phase 4 — Root-cause

- For each ⚠️/❌, **trace through data-flows.md to the actual broken component and its
  upstream cause** — distinguish symptom from cause. ("Search canary fails" is a
  symptom; the cause might be a stale index, which itself is caused by a wedged
  vault-sync.) Report the cause, list the symptoms it explains.
- Collapse co-symptoms under one root cause so the remediation plan fixes causes, not
  symptoms.
- **Tag each finding `shared-code` vs `this-brain`.** Brains run a common code repo
  (clones of the same `.claude/scripts`/`hooks`), so a defect in that code affects EVERY
  brain — fix it once in the repo and it propagates on pull. A `this-brain` finding is
  config / state / parity local to the brain being diagnosed (hand it to that brain's
  owner; never reach into another brain to fix it). Before tagging `shared-code`, verify
  the defect against the actual repo file (a finding can look local but be a shared bug —
  e.g. a `block-secrets` pattern gap surfaced by a LisaOS run was a latent BrunOS gap too).
  This tag is the routing decision: shared-code → one PR to the common repo; this-brain →
  the owner's punch-list.

### Phase 5 — Report + ordered remediation plan

Copy [assets/diagnosis-report-template.md](assets/diagnosis-report-template.md) and fill
it with real evidence. Produce:

1. **The scorecard** — per-subsystem ✅/⚠️/❌ with counts and the doc-05 summary line
   (`CRITICAL: n · DEGRADED: n · INFO: n`), release-blockers flagged.
2. **The remediation plan** — ordered, atomic, **independently testable** tasks (mirror
   how BrunOS plans are structured): each task has a goal, the file(s) to touch, a
   `--dry-run`/manual validation that proves it, and an acceptance condition.
   Release-blockers (Phase 2 failures) come first; then critical → degraded → info.

Write both to the brain's plans directory (mirror where it keeps build plans, e.g.
`.agent/plans/diagnosis-<brain>-<YYYY-MM-DD>.md`).

### Phase 6 — Hand off (do not auto-fix)

Present the report. **Do not implement fixes during diagnosis.** Let the user trigger
remediation — per-run they decide whether to spin a `dev-task`, draft ClickUp cards, or
fix inline. For each remediation task, default to **adopting the reference implementation**
(port the working module from BrunOS) over re-implementing, confirming against what
exists locally first.

## Role applicability (gate the checklist on Phase-0 role)

| Subsystem | Individual brain | Company brain |
|---|---|---|
| A Foundation | ✅ | ✅ |
| B Memory / RAG | ✅ | ✅ |
| C Consolidation | C1–C5 | C1–C5 **+ C6** (company reflect/dream) |
| D Proactive (heartbeat/drafts/habits/gap) | ✅ | ❌ skip (no heartbeat) |
| E Integrations | ✅ | as configured |
| F Chat | F1–F3 | F1–F3 **+ F4** (profile/registry/allowlist) |
| G Federation **producer** | ✅ if `reflection.federation == true`, else skip | ❌ skip |
| H Federation **consumer** | ❌ skip | ✅ |
| I Security | ✅ | ✅ |
| J Deploy / Monitoring | ✅ | ✅ |

## Notes

- If a check needs a command this brain doesn't expose, report it as "could not verify
  — manual check needed" rather than inventing a result.
- A solo individual brain with `federation:false` legitimately has no G/H units — that
  is a ✅ (correctly absent), not a ❌.
- Severity policy (from the checklist): failures in **G2/G3/H2/I1/I5/J6** (privacy,
  federation gates, single-instance) are **release-blockers**. Everything else is
  fix-forward.
