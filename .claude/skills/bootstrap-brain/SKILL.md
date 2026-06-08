---
name: bootstrap-brain
description: >-
  State-aware reconciler that brings a second brain to a healthy, complete state — either
  by RECONCILING an existing, lagging brain to parity (the common case: bring LisaOS or a
  client brain up to date) or standing one UP FROM SCRATCH (greenfield, bare host). Uses
  diagnose-brain as its detect phase, turns its remediation plan into a work-list, then
  applies known-exact implement-actions (port-kits: portable hook paths, the path-boundary
  guard, vault-skill seeding, shared-code fixes, env fixes, units/seeds) — idempotently,
  dry-run → approve → apply → re-validate. Use when asked to "bootstrap a brain", "bring
  this brain up to parity", "set up a new brain", "fix everything diagnose found", "onboard
  a client/employee brain", or to reconcile a lagging brain. Runs INSIDE the target brain;
  never reaches into another brain. Role-aware (individual/producer vs company/consumer),
  per-host. It WRITES — so it shows the plan and waits for approval before mutating.
---

# Bootstrap Brain (state-aware reconciler)

Bring the brain you are running inside to a **healthy, complete state**. This is the
**implement** half of the reconciler; `diagnose-brain` is the **detect** half. Together they
are the four-phase loop from `onboarding_installer.md`:

```
DETECT (diagnose-brain) → DELTA (its remediation plan) → IMPLEMENT-ONLY-MISSING (port-kits) → VALIDATE (re-diagnose)
```

Two starting states, **one skill**:
- **Reconcile** — an existing, lagging brain (e.g. LisaOS): detect finds most things present →
  implement only the gaps. The common case.
- **Greenfield** — a bare/fresh brain: detect finds little/nothing present → implement the
  full bring-up. The degenerate case (same checklist, more actions).

## Hard rules

1. **Runs INSIDE the target brain.** It mutates *this* brain's repo/vault/host. It is
   self-contained + portable (copy it to the brain you're bootstrapping, like diagnose-brain).
   **Never** open, read, or write another brain's vault/repo/home.
2. **It WRITES — so: dry-run → approve → apply.** Always print the full action plan first
   (what it will change, where). Get explicit approval. Then apply. On a *live* brain this is
   mandatory — you're touching a running system.
3. **Idempotent, implement-only-missing.** Re-runnable. Never clobber done work — each action
   first checks whether its target diagnose-check already passes and **skips if so**. A
   half-bootstrapped brain re-run only does what's left.
4. **Dependency-ordered.** Some actions must precede others (the canonical example: fix the
   hook *paths* before installing the path-boundary guard, or the guard wires to a dead path
   and silently no-ops). The action catalog declares each action's prerequisites.
5. **Per-host.** A brain often lives on ≥2 hosts (VPS + Mac). Host-specific actions (hook
   paths, brain-local vault skills, OAuth token) must run on *each* host. State which host
   you're on and which actions are host-scoped.

## Inputs

- **Mode** (optional) — `reconcile` | `greenfield`; auto-detected from the diagnose report
  (lots present → reconcile; near-empty → greenfield). Override if you know.
- **A diagnose-brain report** (optional) — if the operator already ran it, reuse it; else this
  skill runs diagnose-brain itself in Phase 1.
- **Role** — read from `brain-config.json` (`individual`/producer vs `company`/consumer); gates
  which seeds/units apply.

## References

- [references/implement-actions.md](references/implement-actions.md) — the **action catalog**:
  each known-exact implement-action, the diagnose-check it closes, its prerequisites,
  idempotency check, the exact steps, and how to validate it. This is "execute exactly how it
  should be."
- `assets/` — bundled port-kit artifacts the actions install verbatim (e.g.
  `assets/path-boundary.py`). Self-contained so the skill works on a **separate-repo** brain.
- The spec + recorded reference runs: the target brain's `onboarding_installer.md` (the LinOS
  C.5 company-greenfield run is logged there); `diagnose-brain` for the checklist.

## Workflow

### Phase 0 — Orient

- Confirm which brain, its **role** (`brain_config.get("role")`), and the **host** you're on.
- Decide **reconcile vs greenfield** (auto from the detect result).
- State the boundary: "I will bootstrap only <this brain>, on <this host>, write-with-approval."

### Phase 1 — Detect (diagnose-brain)

- Run `diagnose-brain` (or ingest a fresh report the operator already produced). Its **scored
  report + ordered remediation plan** is the input to the delta. Each ❌/⚠️ is a candidate action.

### Phase 2 — Delta (build the work-list)

- Map each failing/ degraded check to an implement-action in the catalog. **Skip checks that
  already pass** (implement-only-missing).
- **Route by the finding's scope tag** (from diagnose-brain):
  - `this-brain` (config/state/parity) → an action that fixes it here.
  - `shared-code` → **does this brain share the canonical code repo (pull) or is it a separate
    repo (port)?** A clone of the shared repo → the fix arrives via `git pull` (note it, don't
    re-apply). A **separate-repo brain** (e.g. LisaOS) → **port** the artifact from `assets/` /
    the reference (the fix won't arrive by pull). This port-vs-pull decision is the reconciler's,
    not the diagnosis's.
  - greenfield-provision → the bring-up actions (user/clone/seed/units/oauth).
- **Order the actions by declared prerequisites** (e.g. portable-hook-paths → path-boundary-guard).
- Produce the ordered, dry-runnable plan.

### Phase 3 — Implement (dry-run → approve → apply)

- **Dry-run the whole plan first**: print each action, the exact change, the target path/host,
  and whether it's a write/install/seed. No mutation yet.
- **Get explicit approval.** (On a live brain, per-action approval for anything destructive or
  ambiguous.)
- **Apply in order, idempotently.** Each action: re-check its precondition → if the target check
  already passes, skip → else apply the exact steps → record what was done. A failed action
  halts its dependents (don't install the guard if portable-hook-paths failed).

### Phase 4 — Validate (re-diagnose)

- Re-run `diagnose-brain`. Confirm the targeted ❌/⚠️ cleared; surface any that didn't.
- Loop Phase 2–4 on remaining gaps until clean (or hand the residue back to the operator).
- Output a before/after: which checks flipped to ✅, which remain, what's deferred.

## Role + mode applicability

- **Individual / producer** (BrunOS/LisaOS shape): proactive units (heartbeat), producer
  federation (clear pipeline), the brain-local vault skills, the path-boundary guard for its
  daemons. Greenfield adds: user/namespace, clone+`uv sync`, vault git-sync, identity seed
  (SOUL/USER/MEMORY/HEARTBEAT/HABITS), `gen_schedules.py` → units, OAuth, healthchecks.
- **Company / consumer** (LinOS shape): consumer + company-reflect units, company seed
  (SOUL/COMPANY/STANDARDS/DECISIONS/ROUTINES/ACCESS_POLICY), channel registry, cleared-inbox
  transport. The LinOS C.5 run in `onboarding_installer.md` is the recorded greenfield reference.

## Notes

- **Greenfield bare-host actions are partially recorded** (LinOS company run logged; the
  *producer* greenfield gets recorded on its first dogfood). Until then, this skill's reconcile
  actions are complete; greenfield actions are added from `onboarding_installer.md` + `deploy/bin/`
  as they're proven. Don't fabricate an un-recorded provisioning step — flag it for the manual run.
- The manual pre-step (buy/provision the VPS, hand over host URL+creds) is **always** out of
  scope — bootstrap starts post-purchase.
