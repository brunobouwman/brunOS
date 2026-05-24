---
name: federation-parity-audit
description: >-
  Self-audit a personal second brain (e.g. LisaOS) against the BrunOS↔LinOS
  federation PRODUCER contract, then emit a CURRENT-STATE report + an ordered PRD
  of parity gaps and deferred follow-ups. Use when bringing an individual brain to
  federation parity — "what's missing on my side for the LinOS federation", "audit
  my brain against BrunOS", "does my vault produce the right capture / share_status
  shape", "make my reflection / inbox / dreaming match", "federation parity audit".
  Runs entirely locally and NEVER reads across the BrunOS↔LisaOS boundary (it audits
  only the brain it is invoked inside).
---

# Federation Parity Audit

Audit the brain you are running inside against the BrunOS↔LinOS federation **producer
contract** — the narrow interface a downstream joint brain (LinOS) consumes — and
produce a gap report + an ordered, implement-one-at-a-time PRD.

## The one hard rule

**Audit only THIS brain.** Never open, read, clone, or infer the contents of another
person's brain (BrunOS must not read LisaOS; LisaOS must not read BrunOS). All evidence
comes from the local vault + local code repo. Joint context lives in LinOS, not here.

## What "parity" means here (and what it does NOT)

Parity = **the federation contract only** — the capture/frontmatter/`share_status`
shape, the reflection inbox stage, and transport. It does **NOT** mean cloning the
other brain byte-for-byte. Internals legitimately differ per brain: systemd namespace
(`lisaosbrain-*` vs `brunoosbrain-*`), vault git repo, file paths, Slack app, and which
optional integrations / heartbeat / chat-bot each person runs. **Verify the contract,
not identical files.** Read [references/federation-contract.md](references/federation-contract.md)
for the exact requirements and how to check each.

## Workflow

Run these steps in order. Each contract check is read-only.

### 0. Orient

- Confirm which brain this is and locate its two roots: the **vault** (the `Memory/`
  tree) and the **code repo** (the `.claude/scripts/` + `.claude/hooks/` tree). They may
  be one repo or two. If either is ambiguous, ask the user — do not guess.
- State the boundary out loud: "I will audit only <this brain>; I will not read any other brain."

### 1. Load the contract

Read [references/federation-contract.md](references/federation-contract.md). It lists
each requirement (C1–C10) with: what it is, why LinOS needs it, the exact local check to
run, and the present / partial / missing rubric.

### 2. Gather evidence

For each requirement, run its check against THIS brain only. Prefer concrete evidence: a
real sample capture's frontmatter, the actual hook block in a repo's
`settings.local.json`, the real functions in `memory_reflect.py`, the real git remote /
sync unit. Record file path + line for every finding. Run any `--dry-run` checks
read-only — they must not write to the vault (verify with `git status` after).

### 3. Score each requirement

Mark each **present / partial / missing** with file-level evidence and a one-line gap
statement. "Partial" needs a specific note on what's incomplete (e.g. "captures tagged
`linos-protostack` but reflection never stamps `share_status`").

### 4. Pull the deferred follow-ups

Read [references/followups.md](references/followups.md). These are deferred on BrunOS too
— they belong in the PRD's "Deferred follow-ups" section, NOT as parity gaps. Carry them
onto this brain's roadmap with the same blocked-on notes, marking which are joint (built
once on LinOS) vs per-brain.

### 5. Write the report

Copy [assets/audit-report-template.md](assets/audit-report-template.md) and fill it in →
write `CURRENT-STATE.md` + `federation-parity-prd.md` into this brain's plans directory
(mirror where it keeps build plans, e.g. `.agent/plans/`). PRD tasks must be **ordered,
atomic, and independently testable**, each with its own `--dry-run` / manual validation —
mirroring how BrunOS plans are structured and executed.

### 6. Hand off

Do not implement during the audit. Present the report; let the user implement tasks **one
at a time**, dry-run-validating each before the next (build a piece → `--dry-run` →
confirm no unintended vault writes → continue). For each task decide clone-vs-adapt per
component: since infra is similar across these brains, default to **adopting the
reference code** (copy/port the module) over re-implementing — but confirm at
implementation time against what already exists locally.

## Notes

- The contract is **producer-side and buildable now**; the end-to-end loop also needs the
  LinOS consumer, which is blocked on LinOS becoming an agent node. Surface that
  dependency in the PRD so this brain isn't over-built ahead of what can read it.
- If a check needs a command this brain's tooling doesn't expose, report it as "could not
  verify — manual check needed" rather than inventing a result.
