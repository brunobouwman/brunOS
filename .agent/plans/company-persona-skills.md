# Company Persona Skills — build plan & shared contract (the stage)

**ClickUp:** `86ca5c6nz` (BaaS company-brain skills pack). **Vault sources:**
`Memory/projects/Brain/company_brain_personas_as_skills.md` (locked spec),
`company_brain_seed_contract.md` (governance corpus + Judge rubric),
`company_brain_channel_registry.md` (routing/access). **External grounding:**
Obra `superpowers` (`requesting-code-review`, `verification-before-completion`),
garrytan `gstack` (persona-as-review-gate).

This doc is the **stage** for the persona-skill pack: the architecture, the shared
contract every persona conforms to, the access model, the build order, and what is
deferred. Concrete skills built in the first pass: **`company-judge`** (deep) and
**`company-query`** (base).

---

## 1. Core decision (from the vault, restated)

A company-brain **persona is a governed skill, not a chat mask.** Each persona =

```
skill procedure  +  brain-config entry  +  access-policy gate
                 +  scoped retrieval     +  output contract  +  action boundary
```

Not "talk like a CFO." A persona is an **operating mode with a job, an evidence set,
permissions, and an output format.** The role/rubric lives in the SKILL.md; the scope
and behavior knobs live in `brain-config.json`; source visibility is governed by
`ACCESS_POLICY.md`.

## 2. The shared persona-skill contract

Every persona SKILL.md declares these eight things (the personas note's "each skill
should declare"). This is the contract the diagnosis/onboarding tooling can check:

| # | Declaration | Notes |
|---|---|---|
| 1 | **Trigger conditions** | explicit `persona=<name>` request, a scheduled routine, or a fallback (default = `company-query`) |
| 2 | **Required inputs** | artifact / question + **caller identity & tier** |
| 3 | **Mandatory context files** | governance files it MUST load (e.g. Judge → `STANDARDS.md` + `DECISIONS.md`); absent → fail-closed/not-applicable |
| 4 | **Retrieval scope** | allowed source path-prefixes only; tier-scoped; never read outside |
| 5 | **Output schema** | the structured artifact it returns + where it's written |
| 6 | **Action boundary** | internal-write targets; external = **draft-only** by default |
| 7 | **Fail-closed behavior** | unknown caller/tier/source/status → refuse + log |
| 8 | **Examples** | at least one worked trigger→output |

## 3. Access model (tiers, fail-closed)

From `ACCESS_POLICY.md` / the seed contract. Personas resolve the **caller tier** and
scope disclosure accordingly:

- **Full** — all company knowledge unless explicitly excluded.
- **Exec** — company-wide + cross-department synthesis; excludes restricted entities + department-private.
- **Dept** — only that department's scoped material + shared company material.
- **None** — no direct brain output.

**Deny-by-default.** Unknown user, unknown department, unknown source, unknown share
status, or unknown export target ⇒ **no surfacing, no integration** until a Full-tier
operator fixes config. Personas never expose material above the caller's tier.

## 4. Runtime resolution flow (condensed from the spec, 10 steps)

```
trigger → resolve persona → load brain-config (persona settings)
       → check ACCESS_POLICY (caller tier; fail-closed if unknown/below-tier)
       → build retrieval scope (persona allowed_sources → path-prefixes + mandatory files)
       → retrieve evidence (scoped memory_search + load governance files, with citations)
       → assemble prompt (SOUL + persona SKILL + access constraints + evidence + output contract)
       → generate structured output
       → action gate (internal write if permitted; external = draft-only; high-risk → Full-tier review)
       → audit/provenance (path, persona, sources, caller, timestamp, refused scope)
```

## 5. Config shape (`brain-config.json` `personas` block)

```jsonc
"personas": {
  "company-judge":  { "enabled": true, "skill": "company-judge",
                      "required_tier": "Full",
                      "allowed_sources": ["standards","decisions","projects","clients","team"],
                      "write_targets": ["digests/judge"], "external_action": "draft-only" },
  "company-query":  { "enabled": true, "skill": "company-query",
                      "required_tier": "Dept", "allowed_sources": "tier-scoped",
                      "write_targets": [], "external_action": "answer-only" }
}
```

Personas read this at runtime; cadence-driven ones are emitted by `gen_schedules.py`
from `ROUTINES.md` later. A persona with no config entry is **disabled** (fail-closed).

## 6. Governance wiring (what the personas read, NOT infer)

`SOUL.md` governs the agent (neutral institutional voice, draft-by-default).
`STANDARDS.md` governs the company. `DECISIONS.md` is append-only case law.
`ACCESS_POLICY.md` is who-sees-what. `_excluded-people.md` is the deny-list.
**The Judge cites `STANDARDS.md`/`DECISIONS.md`; it never infers a standard from
SOUL.** Conflicts (standard vs decision) are flagged, never silently resolved.

## 7. Role gating + a reconciliation note

These skills are **company-role only** — they read company-vault governance files that
don't exist in an individual brain. Gate on `brain_config.get("role") == "company"`
(matches `brain_config.py` DEFAULTS + the diagnose-brain checklist). **Reconcile:** the
seed-contract template writes `"role": "company-brain"` — align that to `"company"` so
code, diagnosis, and seed agree (small follow-up on the LinOS vault seed).

Invoked inside an individual brain (no `STANDARDS.md`), a persona returns
**not-applicable / fail-closed**, never a guessed answer.

## 8. Build order

1. **`company-judge`** — first, because it's the differentiator (nothing reviews work
   against standards/decisions today). ✅ this pass (deep).
2. **`company-query`** — the fail-closed scoped-answer baseline; demonstrates the
   access-tier gate. ✅ this pass (base).
3. **`company-leadership-digest` / `company-gap-analyst`** —
   **WRAP `company_brain_reflect.py`, don't rebuild.** ✅ this pass (base). Confirmed:
   one `company_brain_reflect.py reflect --profile <p>` run writes BOTH
   `digests/leadership/<ISO-week>.md` AND `digests/gaps/<date>.md` from a single Sonnet
   call. The two personas are governed front-ends over that **same** run (run once,
   surface both slices) — they add tier resolution, the tier-scoped surface, and
   draft-only/internal-only delivery, never new extraction.
4. **`company-consolidator`** — ✅ built (base). Governed front-end over the **consumer loop
   `linos_consumer.py`** (NOT `company_brain_reflect.py` — that's the digest layer; the
   consumer is the integration engine: dual-gate read → `joint/` + `LINMEMORY.md` + acks).
   Adds provenance-preservation, conflict-separation, decision-candidate flagging, fail-closed.
5. **`company-standards-review`** — ✅ built (base). Net-new governance-maintenance judgment:
   reads recurring `digests/judge/` findings + reversed decisions + `digests/gaps/`, **proposes**
   `STANDARDS.md`/`DECISIONS.md` edits to `digests/standards-review/` for Full-tier approval.
   Never rewrites governance directly; preserves superseded rules.

**Persona set is COMPLETE** — all six personas from the spec are built (judge, query,
leadership-digest, gap-analyst, consolidator, standards-review). Remaining is the shared
plumbing (`personas.py`) + the onboarding-questionnaire-driven custom skills.
5. **`personas.py`** — shared `load_persona` / `resolve_persona` /
   `persona_allowed_sources` / `persona_context` + access-policy tier parsing. Build
   **only once ≥2 personas need shared resolution** (the spec's guidance). The first two
   skills embed what they need; extract to code when the duplication is real. ⏸ deferred.

## 9. Non-duplication + sibling skills

- **Don't duplicate `company_brain_reflect.py`** (digest/gap/consolidation already
  exist) — wrap it.
- **Sibling, not this pack: a `code-review` skill for the individual dev pipeline.**
  `dev-task` (autonomous_dev_skill.md) wants a reviewer gate over **project repos** in
  an individual brain — different evidence corpus (project repo, no company STANDARDS)
  and action surface. It should **reuse `company-judge`'s `review-methodology.md`**
  reference (severity model + evidence-before-claims) but is its own skill. Noted so we
  don't conflate the company Judge with the dev-pipeline reviewer.

## 10. Deferred / open

- `personas.py` + `ACCESS_POLICY.md` structured tier parsing (v1: simple section or
  companion JSON to avoid brittle markdown parsing).
- `memory_search.py` multi-`--path-prefix` (or a fuse helper) for persona scoped retrieval.
- `ROUTINES.md` → `gen_schedules.py` emission of scheduled-persona timer units.
- Channel-registry routing (`company_brain_channel_registry.md`) selecting which persona
  runs per channel — the registry decides *who/where*, the skill defines *behavior*.

## Acceptance (this pass)

- Shared contract + access model + build order documented (this file).
- `company-judge` built deep: 6-check rubric (from seed contract) + superpowers severity
  & evidence model, citations, conflict-flagging, draft-only action boundary, fail-closed,
  output template, worked example.
- `company-query` built as a solid base: tier-scoped, citation-backed, fail-closed,
  answer-only.
- Both pass `quick_validate`; both gate on company role + access tier.
