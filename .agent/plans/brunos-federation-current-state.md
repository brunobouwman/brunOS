# Federation Parity — Current State (BrunOS, 2026-05-24)

**Brain audited:** BrunOS · **Vault root:** `BrunOS/Memory/` · **Code repo:** this repo (`.claude/`)
**Boundary attestation:** audited only BrunOS; no other brain was read.

> Produced by the `federation-parity-audit` skill as a live proof run against the reference
> implementation. BrunOS should — and does — score near-complete on producer logic.

## Scorecard

| # | Requirement | Status | Evidence (file:line) | Gap |
|---|-------------|--------|----------------------|-----|
| C1 | Capture format + frontmatter | **present** | `shared.py:312` `write_inbox_capture`; sample `_inbox/sessions/colinas/2026-05-23-204717-38afd0ea.md` carries all fields | — |
| C2 | `default_export` semantics | **present** | `shared.py:235` `_VALID_EXPORT_TARGETS`; colinas 7×`linos-protostack`, vertik 39×`personal` | — |
| C3 | Capture hooks in repos | **present** | `lab-agent`, `lab-agent-chat-ui`, `colinas` all wire SessionStart+End+PreCompact with correct `--project`/`--default-export` | — |
| C4 | Inbox layout + slug canon | **present** | `_inbox/sessions/{colinas,vertik}/`; `shared.py:254` `_SLUG_ALIASES`, `:267` `canonicalize_slug` | — |
| C5 | Reflection inbox stage | **present** | `memory_reflect.py:615` `_run_inbox_stage` (3 outputs, watermark `:59`, never-delete); dry-run clean | — |
| C6 | `share_status: cleared` gate | **present** | `memory_reflect.py:554` `_strip_and_mark_capture`; validated on a copy (asides stripped, `cleared` stamped) | — |
| C7 | Vault transport | **DEFERRED (real gap)** | `_inbox/` gitignored (`BrunOS/.gitignore:13`); `brunoosbrain-reflect` runs VPS-side — but **no rsync** delivers Mac captures to the VPS | Captures land on the Mac; reflection runs on the VPS; nothing moves them ⇒ F1 |
| C8 | Uniform frontmatter | **partial** | `projects/colinas.md` block-list tags ✓; `projects/vertik.md:5` uses inline `tags: [vertik, contract]` | Some project files use inline-array tags |
| C9 | Recursion guard + setting_sources | **present** | `memory_reflect.py:19` `CLAUDE_INVOKED_BY`; `:171` `setting_sources=None` | — |
| C10 | Confidentiality routing | **present** | vertik 39/39 `personal`; 2 colinas `personal` are Codex (personal by design, stay invisible to LinOS) | — |

## Summary

- **Already done (8/10 fully):** C1–C6, C9, C10 — the entire producer *logic* is in place. BrunOS
  captures correctly, tags by company, canonicalizes slugs, and (as of today) drains the inbox
  with the 3-output strip-in-place reflection stage behind a `share_status: cleared` gate.
- **Partial:** C8 — frontmatter is mostly uniform but at least one project file (`vertik.md`)
  still uses an inline-array `tags`, against the block-list convention.
- **Deferred (not a today-failure, but the binding gap):** C7 — there is **no transport**. The
  inbox stage runs on the VPS (`brunoosbrain-reflect`), but captures are written on the Mac and
  `_inbox/` is host-local/gitignored. **Until F1 (rsync) exists, the VPS inbox stage has nothing
  to process** — or reflection's inbox stage must run on the Mac in the interim.
- **Highest-priority item:** C7 transport (F1). It blocks BrunOS's own newly-built inbox stage
  from doing anything in the VPS-primary deployment.

## Proof-run takeaways (skill self-test)

- The skill's checks all executed and produced real, file-level evidence — no hand-waving.
- It correctly flagged BrunOS as producer-complete **and** surfaced a genuine architectural gap
  (C7) plus a hygiene nit (C8) rather than rubber-stamping. The skill is working.
