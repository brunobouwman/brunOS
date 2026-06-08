---
name: create-second-brain-prd
description: >-
  Elicit a brain's customization (from the requirements template) and emit the STRUCTURED
  ONBOARDING SPEC — brain-config.json + the per-brain variables + the seed content
  (SOUL/USER/STANDARDS/folders) — that the bootstrap-brain skill builds from
  deterministically. Use when onboarding a new brain — yours, a teammate's, or a client's:
  "create my second brain PRD", "generate my brain spec", "onboard a brain", "scope a client
  brain", or after filling the requirements template. Handles individual AND company/client
  brains. It produces the WHAT (config + seeds), NOT the HOW — bootstrap-brain owns the
  secure, uniform build; diagnose-brain validates it. That split is the whole point: it's why
  two people filling the same requirements no longer end up with divergent, possibly-insecure
  setups.
argument-hint: <path-to-requirements> [output-dir]
---

# Second Brain Onboarding Spec Generator

Turn a person's / company's filled-out requirements into the **structured onboarding spec**
that `bootstrap-brain` builds from. This is the **elicit** front-end of onboarding:

```
create-second-brain-prd (ELICIT → config + seeds) → bootstrap-brain (DETERMINISTIC, SECURE BUILD) → diagnose-brain (VALIDATE)
```

## Why this skill changed (read this)

The old version emitted a **broad, phased build PRD that a coding agent then interpreted** —
which is exactly why two people (e.g. Bruno + Lisa) followed the same process and ended up
with **different, inconsistent setups**, and why a broadly-guided agent might skip a hook or
mis-wire a gate. The build is now **deterministic** (`bootstrap-brain` installs the same
battle-tested, secure stack every time). This skill only produces the **customization** — the
config + seed content that *varies* per brain. **A spec can only set config/content; it cannot
produce an insecure or incomplete brain** — the security layers + privacy gates are installed
uniformly by bootstrap and verified by diagnose-brain. That's the fix for both the divergence
and the security risk.

So: keep the requirements elicitation (it captures each client's specifics); drop
requirements-as-build-guide.

## Inputs

- **`$0`** — path to the filled requirements file (the bundled, extended
  `${CLAUDE_SKILL_DIR}/my-second-brain-requirements.md`; copy + fill it first).
- **`$1`** (optional) — output dir for the spec bundle. Default `.agent/plans/brain-spec/<slug>/`.

## Outputs — the onboarding spec bundle (what bootstrap consumes)

1. **`brain-config.json`** — the machine-readable knobs: `role`, `timezone`, `default_language`,
   `action_surface` (from proactivity), `reflection.federation` + federation role, integrations
   **enabled** (from platforms + priority), cadences, `personas` (company), `channels`,
   `comms_capture`, `notify`. (Shape: `brain_config.DEFAULTS` + `Memory/Brain/brain-config.template.json`.)
2. **Per-brain variables** — the parameterization block (company: the "Per-Company Variables"
   YAML from `company_brain_seed_contract.md`; individual: the equivalent — name, vault slug,
   timezone, language, proactivity, federation role/scope).
3. **Seed content** (filled from the answers, in the right voice):
   - **Individual:** `SOUL.md`, `USER.md`, `MEMORY.md` (sparse), `HEARTBEAT.md`, `HABITS.md`.
   - **Company:** `SOUL.md`, `COMPANY.md`/`USER.md`, `LINMEMORY.md`/`COMPANY_MEMORY.md`,
     `STANDARDS.md`, `DECISIONS.md`, `ROUTINES.md`, `ACCESS_POLICY.md`, `_excluded-people.md`,
     `_brain-filing-rules.md` (use the templates in `company_brain_seed_contract.md`).
4. **Folder taxonomy** — derived from the memory categories → the brain's `Memory/` subfolders,
   which `bootstrap-brain` R3 uses to generate the brain-local `vault-structure` + `memory-search`
   skills.

**It does NOT emit** a build narrative, phase plan, or anything an agent free-interprets. The
build is `bootstrap-brain`.

## Workflow

0. **Read requirements + determine shape.** Read `$0`. Resolve: **role** (individual | company),
   **federation** (singleton | producer→a company brain | consumer = is the company brain),
   **self vs client**. If the file is unfilled, point them at the bundled template.
1. **Map answers → `brain-config.json`** (see the mapping table). Use `brain_config.DEFAULTS` as
   the base; set only what the answers specify.
2. **Generate the seed content** from the role's templates, filled from the answers. Company
   uses the **neutral institutional voice** + governance-first SOUL from
   `company_brain_seed_contract.md`; individual uses the personal-brain shape. Never invent
   excluded names; keep STANDARDS/DECISIONS separate from SOUL.
3. **Derive the folder taxonomy** from the memory categories (+ company departments).
4. **Light per-integration check** — for each enabled integration, note its auth method + env
   var names (so the config/`.env` placeholders are right). This is *only* to set integration
   config correctly — **not** to write a build guide. Research a platform only if its auth shape
   is unfamiliar.
5. **Emit the spec bundle** to the output dir. Record **names, never secret values**.
6. **Hand off:** tell the operator to run **`bootstrap-brain` (greenfield)** against this spec —
   it installs the uniform secure stack parameterized by the spec — then **`diagnose-brain`**
   validates it's complete + secure.

## Mapping rules (requirements → config / seeds)

| Requirement answer | → maps to |
|---|---|
| Name / role / daily work / timezone | `USER.md` (or `COMPANY.md`) + `brain-config` timezone/language |
| Vault folder name | vault slug / path; folder taxonomy root |
| Platforms + Integration Priority | integrations **enabled** in `brain-config` + the build order; `.env` placeholders |
| Top Tasks | which optional features + custom skills/personas to enable (NOT a build plan) |
| **Proactivity level** | `action_surface` + SOUL action boundaries + draft-lifecycle on/off (Observer→notify-only … Partner→autonomous-low-risk) |
| **Security boundaries** | SOUL "**NEVER** without approval" list + the path-boundary guard's policy (these are **mandatory/uniform** — security is battle-tested, not optional; the answers tune wording, not whether the gates exist) |
| Memory categories (+ departments) | the `Memory/` folder taxonomy → vault-structure skill |
| Infrastructure (OS / local / VPS) | deployment target, `gen_schedules` platform, single-instance/failover |
| **Role + federation** | the seed set (individual vs company) + which federation units (producer / consumer / none for a singleton) |
| **Company: departments / tiers / standards / personas** | `ACCESS_POLICY.md` tiers, `STANDARDS.md`/`DECISIONS.md` seeds, `personas` enabled, channel registry |

## The security/completeness invariant

Because the build is `bootstrap-brain` (which installs block-secrets / dangerous-bash /
protect-soul / path-boundary, the privacy/federation gates, sanitization, and monitoring, then
re-runs `diagnose-brain` to verify), **a spec produced here cannot result in an insecure or
incomplete brain.** The spec varies only the customization; the infrastructure is uniform. This
is the structural guarantee that replaces "hope the agent followed the PRD correctly."

## References

- [references/output-contract.md](references/output-contract.md) — the exact spec-bundle shape +
  the handoff to `bootstrap-brain`.
- `company_brain_seed_contract.md` (target vault) — company seed templates + per-company variables.
- `onboarding_installer.md` (target vault) — the build spec `bootstrap-brain` follows (incl. the
  recorded LinOS greenfield run).
- `references/architecture-reference.md` — legacy blueprint; the current source of truth is the
  live codebase + the two skills above. Use only for background.
