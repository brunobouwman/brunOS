<!--
Federation parity audit output template. Produce TWO files from this:
  1) CURRENT-STATE.md            — the scored audit (everything above the PRD divider)
  2) federation-parity-prd.md    — the ordered build plan (everything below)
Fill every <…> placeholder with real, file-level evidence from THIS brain only.
Delete this comment in the output.
-->

# Federation Parity — Current State (<brain name>, <YYYY-MM-DD>)

**Brain audited:** <name> · **Vault root:** <path> · **Code repo:** <path>
**Boundary attestation:** audited only this brain; no other brain was read.

## Scorecard

| # | Requirement | Status | Evidence (file:line) | Gap |
|---|-------------|--------|----------------------|-----|
| C1 | Capture format + frontmatter | <present/partial/missing> | <…> | <…> |
| C2 | `default_export` semantics | <…> | <…> | <…> |
| C3 | Capture hooks in repos | <…> | <…> | <…> |
| C4 | Inbox layout + slug canon | <…> | <…> | <…> |
| C5 | Reflection inbox stage | <…> | <…> | <…> |
| C6 | `share_status: cleared` gate | <…> | <…> | <…> |
| C7 | Vault transport | <present/partial/deferred> | <…> | <…> |
| C8 | Uniform frontmatter | <…> | <…> | <…> |
| C9 | Recursion guard + setting_sources | <…> | <…> | <…> |
| C10 | Confidentiality routing | <…> | <…> | <…> |

## Summary

- **Already done:** <one line per present requirement>
- **Partial:** <what's incomplete + the specific missing piece>
- **Missing:** <what's absent>
- **Highest-priority gap:** <usually C10 if any mis-tag, else the lowest-numbered missing contract item>

---

# Federation Parity — PRD (<brain name>)

_Draft for review — refine before implementing. Build one task at a time; dry-run-validate each._

## Parity tasks (ordered, atomic, independently testable)

### Task 1 — <title> (closes C<n>)
- **Goal:** <what reaching contract compliance looks like>
- **Clone-vs-adapt:** <adopt reference module X / port + adjust paths / build fresh> — confirm against what exists locally first
- **Files:** <files to add/edit on THIS brain>
- **Validation:** <`--dry-run` / grep / manual check that proves it> — must show no unintended vault writes (`git status Memory`)
- **Acceptance:** <observable condition>

### Task 2 — <title> (closes C<n>)
<same shape>

<…one task per gap, ordered so each is independently shippable; confidentiality gaps (C10) first…>

## Deferred follow-ups (mirror BrunOS — NOT parity gaps)

<carry F1–F5 from references/followups.md, translated to this brain's namespace, each with
its scope (per-brain / joint) + blocked-on note. Include the "episodic consolidation = CUT"
reminder so it isn't built.>

## Sequencing note

Producer parity (Tasks above) is buildable now. The end-to-end loop additionally needs the
LinOS consumer (F3), blocked on LinOS-as-agent (Phase C.5) — so finish producer parity, then
pause federation build until LinOS can read it.
