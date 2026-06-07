<!--
Diagnosis output template. Produce ONE file from this:
  diagnosis-<brain>-<YYYY-MM-DD>.md   — the scored report + ordered remediation plan
Fill every <…> placeholder with real, file-level evidence from THIS brain only.
Delete this comment in the output. Skip subsystem sections that don't apply to the role.
-->

# Brain Diagnosis — <brain name> (<YYYY-MM-DD>)

**Brain:** <name> · **Role:** <individual | company> · **Host:** <Mac | VPS:bruno | VPS:linos>
**Vault root:** <path> · **Code repo:** <path> · **Federation:** <on | off>
**Trigger:** <full sweep | hint: "<the alert/healthcheck/symptom>">
**Boundary attestation:** diagnosed only this brain, read-only (`git status Memory` clean after dry-runs).

## Scorecard

```
A Foundation        <✅|⚠️|❌> n/n   <one-line note for any non-pass>
B Memory/RAG         <…>
C Consolidation      <…>
D Proactive          <… or "n/a (company role)">
E Integrations       <…>
F Chat               <…>
G Federation(prod)   <… or "n/a (federation off / company role)">
H Federation(cons)   <… or "n/a (individual role)">
I Security           <…>
J Deploy/Monitoring  <…>
─────────────────────────────────────────────
CRITICAL: <n> · DEGRADED: <n> · INFO: <n>
RELEASE-BLOCKERS: <none | list G2/G3/H2/I1/I5/J6/F2 failures>
```

## Findings (failures + degradations only)

| Check | Status | Evidence (file:line / command → output) | Symptom or root cause? |
|---|---|---|---|
| <e.g. B2> | ⚠️ | <newest .md mtime 14:02 vs memory.db 09:40 → 4.3h> | symptom of J5 (vault-sync wedged) |
| <…> | <…> | <…> | <…> |

## Root-cause analysis

For each distinct root cause, the cause + the symptoms it explains (collapse co-symptoms):

- **Root cause 1 — <one line>.** Explains: <which checklist failures>. Trace: <flow + hop>.
- **Root cause 2 — <…>.**

## Critical safety floor

> Always-run release-blocker checks (G2/G3/H2/I1/I5/J6/F2), regardless of the trigger.

- G2 dual-gate: <✅/❌ + evidence> · G3 canary: <…> · H2 read-gate: <…>
- I1 hook order: <…> · I5 privacy-gate test: <…>
- J6 single-instance: <…> · F2 one bot process: <…>

---

# Remediation Plan — <brain name>

_Ordered, atomic, independently testable. Build one task at a time; dry-run-validate each.
Release-blockers first, then critical → degraded → info._

### Task 1 — <title> (fixes <check id>) · severity: <release-blocker|critical|degraded|info>
- **Root cause:** <the cause this fixes, not the symptom>
- **Clone-vs-adapt:** <adopt BrunOS reference module X / port + adjust paths / config-only> — confirm against local first
- **Files:** <files to add/edit on THIS brain>
- **Validation:** <`--dry-run` / grep / `--smoke-test` / manual check that proves it> — must show no unintended vault writes (`git status Memory`)
- **Acceptance:** <observable condition that closes the check>

### Task 2 — <title> (fixes <check id>) · severity: <…>
<same shape>

<…one task per root cause, ordered so each is independently shippable; release-blockers first…>

## Handoff

- **Suggested route:** <dev-task for code fixes · ClickUp cards for ops/tracking · inline for config-only>
- **Do NOT auto-apply** — Bruno triggers remediation per-task.
- **Deferred / could-not-verify:** <checks that needed a command this brain doesn't expose → manual check needed>
